"""Controlled static HTML publication adapter.

The adapter is deliberately file based and local.  A trusted server-side
manifest is the only source of repository, file, and field permissions.  A
request can provide values for those fields, but it cannot choose a path or a
DOM selector.  ``prepare`` reads and renders a proposed change; ``apply`` is
the only method that writes the target file and performs a second
compare-and-swap check immediately before the atomic write.
"""

from __future__ import annotations

import difflib
import hashlib
import html
import json
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from .base import (
    ApplyResult,
    IdempotencyConflictError,
    InspectionResult,
    InvalidPublicationError,
    ManifestError,
    PrepareRequest,
    PreparedPublication,
    PublicationApproval,
    PublicationConflictError,
    PublisherAdapter,
    ReviewRequiredError,
    RollbackConflictError,
    RollbackResult,
    UnauthorizedTargetError,
    canonical_json,
    content_hash,
    utc_now,
)


_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_HEX40_64 = re.compile(r"^[0-9a-fA-F]{40,64}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ATTR_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9:_-]*$")
_VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})
_SAFE_FRAGMENT_TAGS = frozenset({
    "a", "blockquote", "br", "code", "dd", "div", "dl", "dt", "em", "h2", "h3", "h4", "li",
    "ol", "p", "pre", "small", "span", "strong", "sub", "sup", "table", "tbody", "td", "th",
    "thead", "tr", "ul",
})
_SAFE_FRAGMENT_ATTRS = frozenset({"class", "id", "rel", "role", "target", "title"})
_SAFE_ARIA_PREFIX = "aria-"
_SAFE_DATA_PREFIX = "data-"
_SAFE_SCHEMES = frozenset({"", "http", "https", "mailto"})


@dataclass(frozen=True)
class FieldSpec:
    """Manifest description of one editable DOM field."""

    kind: str
    tag: str | None = None
    attrs: Mapping[str, str] = None  # type: ignore[assignment]
    name: str | None = None
    attribute: str = "content"

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", str(self.kind).casefold())
        object.__setattr__(self, "tag", self.tag.casefold() if self.tag else None)
        object.__setattr__(self, "attrs", {str(k).casefold(): str(v) for k, v in (self.attrs or {}).items()})
        object.__setattr__(self, "name", self.name.casefold() if self.name else None)


@dataclass(frozen=True)
class PageManifest:
    repository: Path
    file: str
    fields: Mapping[str, FieldSpec]


@dataclass(frozen=True)
class SiteManifest:
    repository: Path
    pages: Mapping[str, PageManifest]


@dataclass
class _Node:
    tag: str
    start: int
    start_end: int
    end_start: int | None
    end_end: int | None
    attrs: dict[str, str | None]
    parent: int | None
    self_closing: bool = False

    @property
    def complete(self) -> bool:
        return self.end_start is not None and self.end_end is not None


class _DocumentIndex(HTMLParser):
    """Collect exact source spans while leaving source text untouched."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self.source = source
        self.nodes: list[_Node] = []
        self._stack: list[int] = []
        self._line_offsets = [0]
        for match in re.finditer(r"\n", source):
            self._line_offsets.append(match.end())

    def _position(self) -> int:
        line, column = self.getpos()
        if line - 1 >= len(self._line_offsets):
            return len(self.source)
        return self._line_offsets[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        start = self._position()
        raw = self.get_starttag_text() or ""
        node = _Node(
            tag.casefold(),
            start,
            start + len(raw),
            None,
            None,
            {key.casefold(): value for key, value in attrs},
            self._stack[-1] if self._stack else None,
        )
        index = len(self.nodes)
        self.nodes.append(node)
        if node.tag not in _VOID_TAGS:
            self._stack.append(index)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        start = self._position()
        raw = self.get_starttag_text() or ""
        self.nodes.append(
            _Node(
                tag.casefold(),
                start,
                start + len(raw),
                start + len(raw),
                start + len(raw),
                {key.casefold(): value for key, value in attrs},
                self._stack[-1] if self._stack else None,
                self_closing=True,
            )
        )

    def handle_endtag(self, tag: str) -> None:
        end_end = self._position()
        # HTMLParser gives us the position at the start of the end tag.  The
        # original token is found from the tag name so attributes and spacing
        # remain byte-for-byte unchanged in every unrelated part of the file.
        token = re.match(r"</[^>]*>", self.source[end_end:], flags=re.I)
        if token is None:
            return
        end_start = end_end
        end_end += len(token.group(0))
        wanted = tag.casefold()
        for position in range(len(self._stack) - 1, -1, -1):
            node_index = self._stack[position]
            node = self.nodes[node_index]
            if node.tag == wanted:
                node.end_start = end_start
                node.end_end = end_end
                del self._stack[position:]
                return

    def handle_comment(self, data: str) -> None:
        # Comments do not affect node spans.  They are rejected in content
        # fragments below because comments can hide unreviewed markup.
        return


class StaticHtmlAdapter(PublisherAdapter):
    """Apply allowlisted title/meta/body/FAQ edits to a local HTML repository."""

    adapter_version = "static-html-v1"

    def __init__(
        self,
        manifest: Mapping[str, Any] | str | os.PathLike[str],
        *,
        repositories: Mapping[str, str | os.PathLike[str]] | None = None,
        state_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self._manifest_source = Path(manifest).expanduser().resolve() if isinstance(manifest, (str, os.PathLike)) else None
        raw_manifest = self._load_manifest(manifest)
        self._sites = self._parse_manifest(raw_manifest, repositories=repositories, base_dir=self._manifest_source.parent if self._manifest_source else Path.cwd())
        self._prepared: dict[str, PreparedPublication] = {}
        self._applied: dict[str, ApplyResult] = {}
        self._rolled_back: dict[str, RollbackResult] = {}
        self._state_dir = Path(state_dir).expanduser().resolve() if state_dir is not None else None
        if self._state_dir is not None:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            self._load_state()

    @property
    def manifest(self) -> Mapping[str, SiteManifest]:
        """The normalized server-side allowlist (read-only by convention)."""

        return self._sites

    def prepare(self, request: PrepareRequest | None = None, **kwargs: Any) -> PreparedPublication:
        request = self._coerce_request(request, kwargs)
        site, page, target, field_specs = self._resolve_target(request.site, request.page)
        self._validate_request(request, field_specs)
        before_bytes, before = self._read_target(target)
        actual_content_hash = content_hash(before_bytes)
        if actual_content_hash != request.expected_base_content_hash:
            raise PublicationConflictError(
                f"base content hash changed for {request.site}:{request.page}; expected {request.expected_base_content_hash}, got {actual_content_hash}"
            )
        actual_commit = self._git_head(site.repository)
        expected_commit = self._validate_expected_commit(request.expected_base_commit)
        if actual_commit != expected_commit:
            raise PublicationConflictError(
                f"base commit changed for {request.site}:{request.page}; expected {expected_commit}, got {actual_commit}"
            )
        index = self._index(before)
        field_nodes = self._resolve_field_nodes(index, field_specs)
        before_fields = {name: self._field_value(before, node, spec) for name, (node, spec) in field_nodes.items()}
        before_field_hashes = {name: content_hash(value) for name, value in before_fields.items()}
        for name, expected in request.expected_field_hashes.items():
            self._validate_hash(expected, f"expected field hash for {name}")
            if name not in before_field_hashes:
                raise UnauthorizedTargetError(f"field is not allowlisted: {name!r}")
            if before_field_hashes[name] != expected:
                raise PublicationConflictError(f"field hash changed for {name!r}")

        replacements: list[tuple[int, int, str]] = []
        rendered_fields: dict[str, str] = {}
        for name, value in request.fields.items():
            node, spec = field_nodes[name]
            rendered = self._render_field(name, value, spec)
            rendered_fields[name] = rendered
            if spec.kind == "meta":
                start, end = node.start, node.start_end
                updated = self._replace_attribute(before[start:end], spec.attribute, rendered)
                replacements.append((start, end, updated))
            else:
                if not node.complete:
                    raise InvalidPublicationError(f"field {name!r} has no closing DOM element")
                replacements.append((node.start_end, node.end_start or node.start_end, rendered))
        after = self._apply_replacements(before, replacements)
        if after == before:
            raise InvalidPublicationError("publication contains no field change")
        after_index = self._index(after)
        after_nodes = self._resolve_field_nodes(after_index, field_specs)
        after_fields = {name: self._field_value(after, node, spec) for name, (node, spec) in after_nodes.items()}
        after_field_hashes = {name: content_hash(value) for name, value in after_fields.items()}
        fact_hash = content_hash(canonical_json(request.current_facts))
        key = request.idempotency_key or self._default_key(request, actual_content_hash)
        self._validate_key(key)
        artifact_payload = {
            "adapter_version": self.adapter_version,
            "site": request.site,
            "page": request.page,
            "repository": str(site.repository),
            "file": page.file,
            "base_commit": expected_commit,
            "base_content_hash": actual_content_hash,
            "revision_hash": request.revision_hash,
            "current_fact_set_hash": fact_hash,
            "fields": dict(request.fields),
            "field_hashes_before": before_field_hashes,
            "field_hashes_after": after_field_hashes,
        }
        artifact_hash = content_hash(canonical_json(artifact_payload))
        existing = self._prepared.get(key)
        if existing is not None:
            if existing.artifact_hash != artifact_hash:
                raise IdempotencyConflictError(f"idempotency key already belongs to a different prepared artifact: {key}")
            return existing
        provenance = {
            **dict(request.provenance),
            "adapter_version": self.adapter_version,
            "site": request.site,
            "page": request.page,
            "repository": str(site.repository),
            "file": page.file,
            "field_names": sorted(request.fields),
            "expected_base_commit": expected_commit,
            "expected_base_content_hash": request.expected_base_content_hash,
            "actual_base_content_hash": actual_content_hash,
            "revision_hash": request.revision_hash,
            "current_fact_set_hash": fact_hash,
            "artifact_hash": artifact_hash,
            "source": request.provenance.get("source", "unspecified"),
            "is_synthetic": bool(request.provenance.get("is_synthetic", False)),
        }
        prepared = PreparedPublication(
            site=request.site,
            page=request.page,
            repository=str(site.repository),
            file=page.file,
            fields=dict(request.fields),
            field_hashes_before=before_field_hashes,
            field_hashes_after=after_field_hashes,
            expected_base_commit=expected_commit,
            actual_base_commit=actual_commit,
            expected_base_content_hash=request.expected_base_content_hash,
            actual_base_content_hash=actual_content_hash,
            revision_hash=request.revision_hash,
            current_facts=request.current_facts,
            current_fact_set_hash=fact_hash,
            before_content=before,
            after_content=after,
            diff=self._diff(before, after, page.file),
            artifact_hash=artifact_hash,
            provenance=provenance,
            idempotency_key=key,
        )
        self._prepared[key] = prepared
        self._save_state()
        return prepared

    def apply(
        self,
        prepared: PreparedPublication,
        approval: PublicationApproval | Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> ApplyResult:
        if not isinstance(prepared, PreparedPublication):
            raise InvalidPublicationError("apply requires a PreparedPublication returned by prepare")
        approval = self._coerce_approval(prepared, approval, kwargs)
        self._resolve_target(prepared.site, prepared.page)
        key = prepared.idempotency_key or self._default_key(prepared, prepared.actual_base_content_hash)
        self._validate_key(key)
        historical = self._applied.get(key)
        if historical is not None:
            if historical.revision_hash != prepared.revision_hash or historical.applied_content_hash != prepared.content_hash:
                raise IdempotencyConflictError(f"idempotency key already belongs to different applied content: {key}")
            return ApplyResult(
                **{**historical.__dict__, "replayed": True, "changed": False, "provenance": {**historical.provenance, "historical_replay": True}}
            )
        self._assert_approval(prepared, approval)
        site, page, target, field_specs = self._resolve_target(prepared.site, prepared.page)
        current_bytes, current = self._read_target(target)
        current_hash = content_hash(current_bytes)
        if current_hash != prepared.actual_base_content_hash:
            raise PublicationConflictError("target file changed after prepare; create a new revision")
        actual_commit = self._git_head(site.repository)
        if actual_commit != prepared.actual_base_commit:
            raise PublicationConflictError("repository base commit changed after prepare; create a new revision")
        current_index = self._index(current)
        current_nodes = self._resolve_field_nodes(current_index, field_specs)
        current_field_hashes = {name: content_hash(self._field_value(current, node, spec)) for name, (node, spec) in current_nodes.items()}
        for name, expected in prepared.field_hashes_before.items():
            if current_field_hashes.get(name) != expected:
                raise PublicationConflictError(f"field {name!r} changed after prepare; create a new revision")
        self._write_target(target, prepared.after_content)
        result = ApplyResult(
            site=prepared.site,
            page=prepared.page,
            repository=prepared.repository,
            file=prepared.file,
            status="applied",
            revision_hash=prepared.revision_hash,
            applied_content_hash=prepared.content_hash,
            base_content_hash=prepared.actual_base_content_hash,
            base_commit=prepared.actual_base_commit,
            current_fact_set_hash=prepared.current_fact_set_hash,
            idempotency_key=key,
            changed=True,
            replayed=False,
            provenance={**dict(prepared.provenance), "approval": self._approval_provenance(approval)},
        )
        self._applied[key] = result
        self._save_state()
        return result

    def inspect(self, prepared: PreparedPublication | ApplyResult, **kwargs: Any) -> InspectionResult:
        if not isinstance(prepared, (PreparedPublication, ApplyResult)):
            raise InvalidPublicationError("inspect requires a prepared or applied publication")
        site, page, target, field_specs = self._resolve_target(prepared.site, prepared.page)
        expected_hash = prepared.content_hash if isinstance(prepared, PreparedPublication) else prepared.applied_content_hash
        expected_base = prepared.actual_base_content_hash if isinstance(prepared, PreparedPublication) else prepared.base_content_hash
        current_bytes, current = self._read_target(target)
        actual_hash = content_hash(current_bytes)
        mismatches: list[str] = []
        if actual_hash != expected_hash:
            mismatches.append("content_hash")
        actual_commit = self._git_head(site.repository)
        if isinstance(prepared, PreparedPublication):
            expected_fields = prepared.field_hashes_after
        else:
            expected_fields = {}
            stored = self._prepared.get(prepared.idempotency_key)
            if stored is not None:
                expected_fields = stored.field_hashes_after
        try:
            index = self._index(current)
            nodes = self._resolve_field_nodes(index, field_specs)
            fields = {name: self._field_value(current, node, spec) for name, (node, spec) in nodes.items()}
            field_hashes = {name: content_hash(value) for name, value in fields.items()}
        except InvalidPublicationError as exc:
            field_hashes = {}
            mismatches.append(f"document:{exc}")
        for name, expected in expected_fields.items():
            if field_hashes.get(name) != expected:
                mismatches.append(f"field:{name}")
        return InspectionResult(
            site=prepared.site,
            page=prepared.page,
            repository=prepared.repository,
            file=prepared.file,
            status="verified" if not mismatches else "mismatch",
            matches_prepared=not mismatches,
            content_hash=actual_hash,
            expected_content_hash=expected_hash,
            base_content_hash=expected_base,
            actual_base_commit=actual_commit,
            field_hashes=field_hashes,
            mismatches=tuple(mismatches),
        )

    def rollback(
        self,
        applied: ApplyResult | PreparedPublication,
        **kwargs: Any,
    ) -> RollbackResult:
        if isinstance(applied, PreparedPublication):
            key = applied.idempotency_key
            prepared = applied
            source = self._applied.get(key or "")
        elif isinstance(applied, ApplyResult):
            key = applied.idempotency_key
            source = self._applied.get(key) or applied
            prepared = self._prepared.get(key)
        else:
            raise InvalidPublicationError("rollback requires an applied publication")
        if prepared is None or source is None:
            raise PublicationConflictError("rollback requires a successful apply result from this adapter")
        rollback_key = kwargs.get("idempotency_key") or kwargs.get("rollback_id") or f"rollback:{key}"
        self._validate_key(rollback_key)
        existing = self._rolled_back.get(rollback_key)
        if existing is not None:
            return RollbackResult(
                **{**existing.__dict__, "replayed": True, "changed": False, "provenance": {**existing.provenance, "historical_replay": True}}
            )
        _, _, target, _ = self._resolve_target(prepared.site, prepared.page)
        current_bytes, _ = self._read_target(target)
        current_hash = content_hash(current_bytes)
        expected = kwargs.get("expected_current_content_hash") or source.applied_content_hash
        self._validate_hash(expected, "expected current content hash")
        if current_hash != expected:
            raise RollbackConflictError("target changed after apply; rollback expected current content hash no longer matches")
        self._write_target(target, prepared.before_content)
        result = RollbackResult(
            site=prepared.site,
            page=prepared.page,
            repository=prepared.repository,
            file=prepared.file,
            status="rolled_back",
            revision_hash=prepared.revision_hash,
            restored_content_hash=prepared.actual_base_content_hash,
            expected_current_content_hash=expected,
            idempotency_key=rollback_key,
            changed=True,
            replayed=False,
            provenance={
                "adapter_version": self.adapter_version,
                "source_idempotency_key": key,
                "reason": kwargs.get("reason"),
                "source": prepared.provenance.get("source", "unspecified"),
            },
        )
        self._rolled_back[rollback_key] = result
        self._save_state()
        return result

    # ------------------------------------------------------------------
    # Request, manifest, and target validation
    # ------------------------------------------------------------------
    @staticmethod
    def _load_manifest(manifest: Mapping[str, Any] | str | os.PathLike[str]) -> Mapping[str, Any]:
        if isinstance(manifest, Mapping):
            return manifest
        path = Path(manifest).expanduser().resolve()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestError(f"unable to read publisher manifest: {path}") from exc
        if not isinstance(payload, Mapping):
            raise ManifestError("publisher manifest root must be an object")
        return payload

    @classmethod
    def _parse_manifest(
        cls,
        raw: Mapping[str, Any],
        *,
        repositories: Mapping[str, str | os.PathLike[str]] | None,
        base_dir: Path,
    ) -> dict[str, SiteManifest]:
        raw_sites = raw.get("sites", raw)
        if isinstance(raw_sites, Sequence) and not isinstance(raw_sites, (str, bytes, bytearray)):
            entries = {}
            for item in raw_sites:
                if not isinstance(item, Mapping):
                    raise ManifestError("each site manifest entry must be an object")
                name = item.get("site", item.get("id", item.get("name")))
                entries[name] = item
            raw_sites = entries
        if not isinstance(raw_sites, Mapping) or not raw_sites:
            raise ManifestError("publisher manifest must contain at least one site")
        result: dict[str, SiteManifest] = {}
        for site_name, site_raw in raw_sites.items():
            cls._validate_name(site_name, "site")
            if not isinstance(site_raw, Mapping):
                raise ManifestError(f"site manifest {site_name!r} must be an object")
            repository_value = site_raw.get("repository", site_raw.get("repo_root"))
            if isinstance(repository_value, str) and repositories and repository_value in repositories:
                repository_value = repositories[repository_value]
            if repository_value is None:
                raise ManifestError(f"site {site_name!r} is missing repository")
            repository = Path(repository_value).expanduser()
            if not repository.is_absolute():
                repository = (base_dir / repository).resolve()
            else:
                repository = repository.resolve()
            if not repository.is_dir():
                raise ManifestError(f"site repository does not exist: {repository}")
            raw_pages = site_raw.get("pages")
            if isinstance(raw_pages, Sequence) and not isinstance(raw_pages, (str, bytes, bytearray)):
                page_entries = {}
                for item in raw_pages:
                    if not isinstance(item, Mapping):
                        raise ManifestError(f"page entry in {site_name!r} must be an object")
                    page_entries[item.get("page", item.get("id", item.get("name")))] = item
                raw_pages = page_entries
            if not isinstance(raw_pages, Mapping) or not raw_pages:
                raise ManifestError(f"site {site_name!r} must contain pages")
            pages: dict[str, PageManifest] = {}
            for page_name, page_raw in raw_pages.items():
                cls._validate_name(page_name, "page")
                if not isinstance(page_raw, Mapping):
                    raise ManifestError(f"page manifest {page_name!r} must be an object")
                file_value = page_raw.get("file", page_raw.get("path"))
                file_name = cls._normalize_relative_path(file_value)
                fields_raw = page_raw.get("fields", page_raw.get("allowed_fields"))
                if isinstance(fields_raw, Sequence) and not isinstance(fields_raw, (str, bytes, bytearray)):
                    fields_raw = {name: name for name in fields_raw}
                if not isinstance(fields_raw, Mapping) or not fields_raw:
                    raise ManifestError(f"page {site_name}:{page_name} must define fields")
                fields: dict[str, FieldSpec] = {}
                for field_name, descriptor in fields_raw.items():
                    cls._validate_field_name(field_name)
                    fields[field_name] = cls._field_spec(field_name, descriptor)
                pages[page_name] = PageManifest(repository, file_name, fields)
            result[site_name] = SiteManifest(repository, pages)
        return result

    @staticmethod
    def _field_spec(name: str, descriptor: Any) -> FieldSpec:
        normalized = str(name).casefold()
        if isinstance(descriptor, str):
            descriptor = {"kind": descriptor}
        if descriptor is None:
            descriptor = {"kind": normalized}
        if not isinstance(descriptor, Mapping):
            raise ManifestError(f"field {name!r} descriptor must be a string or object")
        kind = str(descriptor.get("kind", normalized)).casefold()
        if "selector" in descriptor:
            selector = str(descriptor["selector"]).strip()
            if selector.casefold() == "title":
                kind = "title"
            elif re.fullmatch(r"meta\[name\s*=\s*['\"]?description['\"]?\]", selector, re.I):
                kind = "meta"
                descriptor = {**descriptor, "name": "description"}
            else:
                marker = re.fullmatch(r"\[data-publisher-field\s*=\s*['\"]?([A-Za-z0-9._-]+)['\"]?\]", selector, re.I)
                if marker:
                    kind = "marker"
                    descriptor = {**descriptor, "marker": marker.group(1)}
                else:
                    raise ManifestError(f"unsupported field selector for {name!r}")
        if kind in {"title", "meta_description", "description"}:
            kind = "meta" if kind != "title" else "title"
        if kind not in {"title", "meta", "element", "marker"}:
            raise ManifestError(f"unsupported field kind {kind!r} for {name!r}")
        attrs = dict(descriptor.get("attrs", {})) if isinstance(descriptor.get("attrs", {}), Mapping) else {}
        if kind == "marker":
            attrs["data-publisher-field"] = str(descriptor.get("marker", normalized))
        tag = descriptor.get("tag")
        if kind == "meta":
            tag = "meta"
        if kind == "title":
            tag = "title"
        if kind == "marker" and tag is None:
            tag = None
        return FieldSpec(kind, str(tag) if tag else None, attrs, str(descriptor.get("name", "description")) if kind == "meta" else None, str(descriptor.get("attribute", "content")))

    @staticmethod
    def _validate_name(value: Any, label: str) -> None:
        if not isinstance(value, str) or not _NAME.fullmatch(value):
            raise ManifestError(f"invalid {label} name")

    @staticmethod
    def _validate_field_name(value: Any) -> None:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", value):
            raise ManifestError("field names must be simple identifiers")

    @staticmethod
    def _normalize_relative_path(value: Any) -> str:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ManifestError("manifest file path must be a non-empty string")
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or re.match(r"^[A-Za-z]:/", normalized):
            raise ManifestError(f"manifest file path must stay repository-relative: {value!r}")
        return path.as_posix()

    def _resolve_target(self, site_name: str, page_name: str) -> tuple[SiteManifest, PageManifest, Path, Mapping[str, FieldSpec]]:
        site = self._sites.get(site_name)
        if site is None:
            raise UnauthorizedTargetError(f"site is not allowlisted: {site_name!r}")
        page = site.pages.get(page_name)
        if page is None:
            raise UnauthorizedTargetError(f"page is not allowlisted: {site_name}:{page_name}")
        if page.repository != site.repository:
            raise ManifestError(f"page repository differs from site repository: {site_name}:{page_name}")
        root = site.repository.resolve()
        self._assert_no_symlink_components(root, page.file)
        target = (root / page.file).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise UnauthorizedTargetError("manifest file resolves outside its repository") from exc
        if target.is_symlink() or not target.is_file():
            raise UnauthorizedTargetError("manifest target must be a regular file and cannot be a symlink")
        return site, page, target, page.fields

    @staticmethod
    def _assert_no_symlink_components(root: Path, relative: str) -> None:
        current = root
        for part in PurePosixPath(relative).parts:
            current = current / part
            if current.is_symlink():
                raise UnauthorizedTargetError(f"manifest target contains a symlink: {relative!r}")

    @staticmethod
    def _coerce_request(request: PrepareRequest | None, kwargs: Mapping[str, Any]) -> PrepareRequest:
        if request is not None:
            if kwargs:
                raise InvalidPublicationError("do not combine a request object and keyword arguments")
            return request
        values = dict(kwargs)
        if "fields" not in values and "changes" in values:
            values["fields"] = values.pop("changes")
        for canonical, aliases in {
            "expected_base_content_hash": ("expected_base_hash", "base_content_hash"),
            "expected_base_commit": ("base_commit",),
            "revision_hash": ("content_revision_hash", "approved_revision_hash"),
            "expected_field_hashes": ("field_hashes",),
        }.items():
            if canonical not in values:
                for alias in aliases:
                    if alias in values:
                        values[canonical] = values.pop(alias)
                        break
        if "current_facts" not in values:
            for alias in ("fact_set", "facts"):
                if alias in values:
                    values["current_facts"] = values.pop(alias)
                    break
        try:
            return PrepareRequest(**values)
        except TypeError as exc:
            raise InvalidPublicationError(f"invalid prepare request: {exc}") from exc

    @staticmethod
    def _coerce_approval(
        prepared: PreparedPublication,
        approval: PublicationApproval | Mapping[str, Any] | None,
        kwargs: Mapping[str, Any],
    ) -> PublicationApproval:
        if approval is not None and kwargs:
            raise InvalidPublicationError("do not combine approval and keyword approval arguments")
        if approval is None:
            values = dict(kwargs)
            if values.pop("approved", False) is True:
                return prepared.approval(approver=values.pop("approver", None), approval_id=values.pop("approval_id", None))
            if not values:
                raise ReviewRequiredError("apply requires an explicit approval for the prepared artifact")
            if "revision_hash" not in values and "approved_revision_hash" in values:
                values["revision_hash"] = values.pop("approved_revision_hash")
            if "fields" not in values:
                values["fields"] = values.pop("approved_fields", values.pop("field_diff", dict(prepared.fields)))
            if "current_facts" not in values:
                values["current_facts"] = values.pop("fact_set", values.pop("facts", prepared.current_facts))
            approval = values
        if isinstance(approval, Mapping):
            fields = approval.get("fields", approval.get("field_diff"))
            if fields is None:
                fields = dict(prepared.fields)
            facts = approval.get("current_facts", approval.get("fact_set", prepared.current_facts))
            try:
                return PublicationApproval(
                    revision_hash=str(approval["revision_hash"]),
                    fields=fields,
                    current_facts=facts,
                    approver=approval.get("approver"),
                    approval_id=approval.get("approval_id"),
                )
            except (KeyError, TypeError) as exc:
                raise ReviewRequiredError("approval must include revision_hash and fields") from exc
        if not isinstance(approval, PublicationApproval):
            raise ReviewRequiredError("unsupported approval type")
        return approval

    @staticmethod
    def _validate_request(request: PrepareRequest, field_specs: Mapping[str, FieldSpec]) -> None:
        if not isinstance(request.site, str) or not isinstance(request.page, str):
            raise UnauthorizedTargetError("site and page must be strings")
        if not isinstance(request.fields, Mapping) or not request.fields:
            raise InvalidPublicationError("at least one allowlisted field change is required")
        unknown = set(request.fields) - set(field_specs)
        if unknown:
            raise UnauthorizedTargetError(f"fields are not allowlisted: {sorted(unknown)!r}")
        if request.current_facts is None:
            raise PublicationConflictError("current fact set must be explicitly bound before publication")
        StaticHtmlAdapter._validate_hash(request.expected_base_content_hash, "expected base content hash")
        StaticHtmlAdapter._validate_hash(request.revision_hash, "revision hash")
        for name, value in request.expected_field_hashes.items():
            if name not in field_specs:
                raise UnauthorizedTargetError(f"field is not allowlisted: {name!r}")
            StaticHtmlAdapter._validate_hash(value, f"expected field hash for {name}")

    @staticmethod
    def _validate_hash(value: Any, label: str) -> None:
        if not isinstance(value, str) or not _HEX64.fullmatch(value):
            raise PublicationConflictError(f"{label} must be a SHA-256 hex digest")

    @staticmethod
    def _validate_expected_commit(value: Any) -> str:
        if not isinstance(value, str) or not _HEX40_64.fullmatch(value):
            raise PublicationConflictError("expected base commit must be a hexadecimal Git object id")
        return value.lower()

    @staticmethod
    def _validate_key(value: Any) -> None:
        if not isinstance(value, str) or not value or len(value) > 255 or any(ord(char) < 32 for char in value):
            raise InvalidPublicationError("idempotency key must be a non-empty printable string")

    @staticmethod
    def _default_key(request: PrepareRequest | PreparedPublication, base_hash: str) -> str:
        return f"{request.site}:{request.page}:{request.revision_hash}:{base_hash}"

    # ------------------------------------------------------------------
    # DOM indexing, field rendering, and safe fragment validation
    # ------------------------------------------------------------------
    @staticmethod
    def _index(source: str) -> _DocumentIndex:
        parser = _DocumentIndex(source)
        try:
            parser.feed(source)
            parser.close()
        except Exception as exc:  # HTMLParser subclasses can raise on malformed input.
            raise InvalidPublicationError("target is not parseable HTML") from exc
        return parser

    @classmethod
    def _resolve_field_nodes(cls, index: _DocumentIndex, specs: Mapping[str, FieldSpec]) -> dict[str, tuple[_Node, FieldSpec]]:
        result: dict[str, tuple[_Node, FieldSpec]] = {}
        for name, spec in specs.items():
            candidates = [node for node in index.nodes if cls._node_matches(node, spec, index.nodes)]
            if len(candidates) != 1:
                raise InvalidPublicationError(f"field {name!r} must resolve to exactly one DOM node, got {len(candidates)}")
            result[name] = (candidates[0], spec)
        return result

    @staticmethod
    def _node_matches(node: _Node, spec: FieldSpec, nodes: Sequence[_Node]) -> bool:
        if spec.tag and node.tag != spec.tag:
            return False
        if spec.kind == "title":
            if node.tag != "title":
                return False
            ancestor = node.parent
            has_head = False
            while ancestor is not None:
                if nodes[ancestor].tag == "svg":
                    return False
                if nodes[ancestor].tag == "head":
                    has_head = True
                    break
                ancestor = nodes[ancestor].parent
            return has_head
        if spec.kind == "meta":
            return node.tag == "meta" and (node.attrs.get("name") or "").casefold() == (spec.name or "description").casefold()
        attrs = dict(node.attrs)
        return all((attrs.get(key) or "").casefold() == value.casefold() for key, value in spec.attrs.items())

    @staticmethod
    def _field_value(source: str, node: _Node, spec: FieldSpec) -> str:
        if spec.kind == "meta":
            return node.attrs.get(spec.attribute.casefold()) or ""
        if node.end_start is None:
            return ""
        return source[node.start_end:node.end_start]

    @classmethod
    def _render_field(cls, name: str, value: Any, spec: FieldSpec) -> str:
        if spec.kind in {"title", "meta"}:
            if not isinstance(value, str) or not value.strip():
                raise InvalidPublicationError(f"{name} must be a non-empty text value")
            if "<" in value or ">" in value:
                raise InvalidPublicationError(f"{name} must not contain HTML markup")
            return html.escape(value, quote=True)
        if name.casefold() == "faq" and isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            blocks: list[str] = []
            for item in value:
                if not isinstance(item, Mapping) or set(item) - {"question", "answer"} or not item.get("question") or not item.get("answer"):
                    raise InvalidPublicationError("FAQ entries must contain only non-empty question and answer")
                question = html.escape(str(item["question"]), quote=False)
                answer = cls._sanitize_fragment(str(item["answer"]))
                blocks.append(f'<div class="faq-item"><h3>{question}</h3><p>{answer}</p></div>')
            return "\n".join(blocks)
        if not isinstance(value, str) or not value.strip():
            raise InvalidPublicationError(f"{name} must be a non-empty text or FAQ value")
        return cls._sanitize_fragment(value)

    @staticmethod
    def _sanitize_fragment(fragment: str) -> str:
        if "<!--" in fragment or "<![CDATA[" in fragment.upper() or "<?" in fragment:
            raise InvalidPublicationError("HTML comments, CDATA, and processing instructions are not allowed")

        class Validator(HTMLParser):
            def __init__(self) -> None:
                super().__init__(convert_charrefs=False)
                self.stack: list[str] = []

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                tag = tag.casefold()
                if tag not in _SAFE_FRAGMENT_TAGS:
                    raise InvalidPublicationError(f"HTML tag is not allowed in publication content: {tag}")
                for key, value in attrs:
                    key = key.casefold()
                    if key.startswith("on") or key in {"style", "src", "srcdoc"}:
                        raise InvalidPublicationError(f"HTML attribute is not allowed: {key}")
                    if key not in _SAFE_FRAGMENT_ATTRS and not key.startswith(_SAFE_ARIA_PREFIX) and not key.startswith(_SAFE_DATA_PREFIX) and key != "href":
                        raise InvalidPublicationError(f"HTML attribute is not allowlisted: {key}")
                    if key == "href":
                        parsed = urlparse(html.unescape(value or "").strip())
                        if parsed.scheme.casefold() not in _SAFE_SCHEMES or parsed.scheme.casefold() in {"javascript", "data", "vbscript"}:
                            raise InvalidPublicationError("links must use a safe HTTP, HTTPS, mailto, or relative URL")
                        if not parsed.scheme and parsed.netloc:
                            raise InvalidPublicationError("protocol-relative links are not allowed")
                    if key == "target" and value not in {None, "_blank", "_self", "_parent", "_top"}:
                        raise InvalidPublicationError("unsupported link target")
                if tag != "br":
                    self.stack.append(tag)

            def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                self.handle_starttag(tag, attrs)
                if self.stack and self.stack[-1] == tag.casefold():
                    self.stack.pop()

            def handle_endtag(self, tag: str) -> None:
                tag = tag.casefold()
                if tag not in _SAFE_FRAGMENT_TAGS or tag == "br" or not self.stack or self.stack[-1] != tag:
                    raise InvalidPublicationError(f"unbalanced HTML tag in publication content: {tag}")
                self.stack.pop()

            def handle_comment(self, data: str) -> None:
                raise InvalidPublicationError("HTML comments are not allowed")

            def handle_decl(self, decl: str) -> None:
                raise InvalidPublicationError("HTML declarations are not allowed")

        validator = Validator()
        try:
            validator.feed(fragment)
            validator.close()
        except InvalidPublicationError:
            raise
        except Exception as exc:
            raise InvalidPublicationError("invalid HTML fragment") from exc
        if validator.stack:
            raise InvalidPublicationError("unbalanced HTML fragment")
        return fragment

    @staticmethod
    def _replace_attribute(start_tag: str, attribute: str, value: str) -> str:
        if not _ATTR_NAME.fullmatch(attribute):
            raise InvalidPublicationError("manifest attribute name is invalid")
        escaped = html.escape(value, quote=True)
        pattern = re.compile(rf"(\b{re.escape(attribute)}\s*=\s*)(['\"])(.*?)\2", flags=re.I | re.S)
        if pattern.search(start_tag):
            return pattern.sub(lambda match: f"{match.group(1)}{match.group(2)}{escaped}{match.group(2)}", start_tag, count=1)
        unquoted = re.compile(rf"(\b{re.escape(attribute)}\s*=\s*)([^\s>]+)", flags=re.I)
        if unquoted.search(start_tag):
            return unquoted.sub(lambda match: f'{match.group(1)}"{escaped}"', start_tag, count=1)
        insertion = f' {attribute}="{escaped}"'
        position = start_tag.rfind("/>")
        return start_tag[:position] + insertion + start_tag[position:] if position >= 0 else start_tag[:-1] + insertion + start_tag[-1:]

    @staticmethod
    def _apply_replacements(source: str, replacements: Sequence[tuple[int, int, str]]) -> str:
        result = source
        for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
            if start < 0 or end < start or end > len(source):
                raise InvalidPublicationError("invalid DOM replacement span")
            result = result[:start] + replacement + result[end:]
        return result

    @staticmethod
    def _diff(before: str, after: str, file_name: str) -> str:
        return "".join(difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=file_name,
            tofile=file_name,
        ))

    # ------------------------------------------------------------------
    # File and Git compare-and-swap helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _read_target(target: Path) -> tuple[bytes, str]:
        try:
            payload = target.read_bytes()
            return payload, payload.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise PublicationConflictError(f"unable to read UTF-8 target file: {target}") from exc

    @staticmethod
    def _write_target(target: Path, content: str) -> None:
        payload = content.encode("utf-8")
        mode = stat.S_IMODE(target.stat().st_mode)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".publish.tmp", dir=str(target.parent))
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, target)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise PublicationConflictError("unable to atomically write target file") from exc

    @staticmethod
    def _git_head(repository: Path) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise PublicationConflictError("Git is required to bind a static publication base commit") from exc
        if result.returncode:
            return None
        value = result.stdout.strip().lower()
        return value or None

    @staticmethod
    def _assert_approval(prepared: PreparedPublication, approval: PublicationApproval) -> None:
        if approval.revision_hash != prepared.revision_hash:
            raise ReviewRequiredError("approval revision hash does not match prepared content")
        if canonical_json(approval.fields) != canonical_json(prepared.fields):
            raise ReviewRequiredError("approved fields differ from prepared content; create a new revision")
        if approval.current_facts is None or canonical_json(approval.current_facts) != canonical_json(prepared.current_facts):
            raise ReviewRequiredError("approved fact set differs from prepared content; create a new revision")

    @staticmethod
    def _approval_provenance(approval: PublicationApproval) -> Mapping[str, Any]:
        return {
            "approval_id": approval.approval_id,
            "approver": approval.approver,
            "revision_hash": approval.revision_hash,
            "fields_hash": content_hash(canonical_json(approval.fields)),
            "fact_set_hash": content_hash(canonical_json(approval.current_facts)),
        }

    # ------------------------------------------------------------------
    # Optional external state for replay across adapter processes
    # ------------------------------------------------------------------
    def _state_path(self, key: str) -> Path:
        assert self._state_dir is not None
        return self._state_dir / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()}.json"

    def _save_state(self) -> None:
        if self._state_dir is None:
            return
        # State is kept outside the target repository by default.  It records
        # enough metadata for idempotent replay while the prepared content is
        # kept in memory; callers wanting process recovery should retain the
        # review artifact in their own durable audit store.
        payload = {
            "applied": {
                key: {
                    **value.__dict__,
                    "applied_at": value.applied_at.isoformat(),
                }
                for key, value in self._applied.items()
            },
            "rolled_back": {
                key: {
                    **value.__dict__,
                    "rolled_back_at": value.rolled_back_at.isoformat(),
                }
                for key, value in self._rolled_back.items()
            },
        }
        path = self._state_dir / "static-html-history.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(canonical_json(payload), encoding="utf-8")
        os.replace(temporary, path)

    def _load_state(self) -> None:
        if self._state_dir is None:
            return
        path = self._state_dir / "static-html-history.json"
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        # Durable replay records intentionally remain metadata-only.  A new
        # process cannot safely rollback without the prepared before-image,
        # so rollback remains bound to the process that prepared the artifact.
        for key, raw in (payload.get("applied", {}) if isinstance(payload, Mapping) else {}).items():
            try:
                self._applied[key] = ApplyResult(
                    site=raw["site"], page=raw["page"], repository=raw["repository"], file=raw["file"], status=raw["status"],
                    revision_hash=raw["revision_hash"], applied_content_hash=raw["applied_content_hash"], base_content_hash=raw["base_content_hash"],
                    base_commit=raw.get("base_commit"), current_fact_set_hash=raw["current_fact_set_hash"], idempotency_key=raw["idempotency_key"],
                    changed=bool(raw.get("changed", True)), replayed=False, provenance=raw.get("provenance", {}),
                    applied_at=utc_now(),
                )
            except (KeyError, TypeError):
                continue


# Explicit names used by callers and fixtures.
StaticHtmlPublisher = StaticHtmlAdapter
StaticHtmlManifest = SiteManifest


__all__ = [
    "FieldSpec",
    "PageManifest",
    "SiteManifest",
    "StaticHtmlAdapter",
    "StaticHtmlManifest",
    "StaticHtmlPublisher",
]
