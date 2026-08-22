from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Callable, NamedTuple

import pytest

ROOT = Path(__file__).resolve().parents[1] / "v2_ops" / "static"
VOID_ELEMENTS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
EXPECTED_IDS = {
    "app-sidebar", "sidebar-backdrop", "mobile-menu", "sidebar-close",
    "nav-overview", "nav-execution", "live-state", "source-health-state",
    "generated-at", "range-select", "refresh-dashboard", "operator-menu",
    "operator-popover", "dashboard-alert", "overview-view", "kpi-grid",
    "execution-series", "status-distribution", "trace-distribution",
    "milestones-chart", "top-node-types", "lead-search", "status-filter",
    "completeness-filter", "result-count", "operations-title", "empty-state",
    "execution-table-body", "execution-mobile-list", "drawer-backdrop",
    "execution-drawer", "drawer-close", "drawer-title", "drawer-lead",
    "drawer-status", "drawer-trace", "drawer-summary", "canvas-title",
    "execution-canvas", "edges", "nodes", "fit-canvas", "zoom-in",
    "zoom-out", "node-title", "input-panel", "input-summary", "input-full",
    "load-full-input", "output-panel", "output-summary", "output-full",
    "load-full-output", "metadata", "node-error",
}


class InteractiveContract(NamedTuple):
    tag: str
    element_id: str | None
    effective_type: str | None
    effective_destination: str | None
    effective_owner: tuple[object, ...] | None
    effective_submit_method: str | None
    enabled: bool
    role: str | None
    tabindex: str | None
    contenteditable: str | None
    stable_identity: tuple[str, str, str | None] | None
    active: bool = True
    actionable: bool = False
    successful: bool = False


LOGOUT_OWNER = ("form", 0, None, "post", "/ops/logout")
EXPECTED_CONTROLS = (
    InteractiveContract("button", None, "submit", "/ops/logout", LOGOUT_OWNER, "post", True, None, None, None, None, True, True, False),
    InteractiveContract("button", "drawer-close", "button", None, None, None, True, None, None, None, None),
    InteractiveContract("button", "fit-canvas", "button", None, None, None, True, None, None, None, None),
    InteractiveContract("button", "load-full-input", "button", None, None, None, True, None, None, None, None),
    InteractiveContract("button", "load-full-output", "button", None, None, None, True, None, None, None, None),
    InteractiveContract("button", "mobile-menu", "button", None, None, None, True, None, None, None, None),
    InteractiveContract("button", "nav-execution", "button", None, None, None, False, None, None, None, None),
    InteractiveContract("button", "nav-overview", "button", None, None, None, True, None, None, None, None),
    InteractiveContract("button", "operator-menu", "button", None, None, None, True, None, None, None, None),
    InteractiveContract("button", "refresh-dashboard", "button", None, None, None, True, None, None, None, None),
    InteractiveContract("button", "sidebar-close", "button", None, None, None, True, None, None, None, None),
    InteractiveContract("button", "sidebar-backdrop", "button", None, None, None, True, None, None, None, None),
    InteractiveContract("button", "zoom-in", "button", None, None, None, True, None, None, None, None),
    InteractiveContract("button", "zoom-out", "button", None, None, None, True, None, None, None, None),
    InteractiveContract("div", "execution-canvas", None, None, None, None, True, "region", "0", None, None),
    InteractiveContract("input", None, "hidden", None, LOGOUT_OWNER, None, True, None, None, None, None, True, False, True),
    InteractiveContract("input", "lead-search", "text", None, None, None, True, None, None, None, None),
    InteractiveContract("select", "completeness-filter", None, None, None, None, True, None, None, None, None),
    InteractiveContract("select", "range-select", None, None, None, None, True, None, None, None, None),
    InteractiveContract("select", "status-filter", None, None, None, None, True, None, None, None, None),
    InteractiveContract("summary", None, None, None, None, None, True, None, None, None, ("Erro sanitizado", "details", "node-error")),
    InteractiveContract("summary", None, None, None, None, None, True, None, None, None, ("Metadados técnicos", "details", "metadata")),
)

FORM_ASSOCIATED_TAGS = {"button", "fieldset", "input", "object", "output", "select", "textarea"}


@dataclass
class Element:
    tag: str
    attrs: dict[str, str | None]
    parent: Element | None = None
    children: list[Element] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)

    def descendants(self, tag: str | None = None) -> list[Element]:
        found: list[Element] = []
        for child in self.children:
            if tag is None or child.tag == tag:
                found.append(child)
            found.extend(child.descendants(tag))
        return found

    def text(self) -> str:
        return "".join(self.text_parts) + "".join(child.text() for child in self.children)


class LocalDOMParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("#document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = Element(tag, dict(attrs), self.stack[-1])
        self.stack[-1].children.append(element)
        if tag not in VOID_ELEMENTS:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_ELEMENTS:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        assert len(self.stack) > 1 and self.stack[-1].tag == tag, f"unexpected </{tag}>"
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        self.stack[-1].text_parts.append(data)

    def finish(self) -> Element:
        self.close()
        assert self.stack == [self.root], "unclosed HTML elements"
        return self.root


def assets() -> tuple[str, str, str]:
    return tuple(
        (ROOT / name).read_text(encoding="utf-8")
        for name in ("index.html", "ops.js", "ops.css")
    )


def javascript_function_body(js: str, name: str) -> str:
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", js)
    assert match is not None, f"missing JavaScript function: {name}"
    depth = 1
    index = match.end()
    while index < len(js) and depth:
        if js[index] == "{":
            depth += 1
        elif js[index] == "}":
            depth -= 1
        index += 1
    assert depth == 0, f"unclosed JavaScript function: {name}"
    return js[match.end():index - 1]


def assert_task2_javascript_contract(js: str) -> None:
    refresh = javascript_function_body(js, "refreshDashboard")
    assert refresh.index("if (state.dashboardLoading) return") < refresh.index("state.dashboardLoading = true")
    assert refresh.index("state.dashboardLoading = true") < refresh.index('$("refresh-dashboard").disabled = true')
    assert re.search(r"try\s*\{\s*await loadDashboard\(\);\s*\}\s*finally\s*\{", refresh)
    finally_body = refresh.split("finally", 1)[1]
    assert "state.dashboardLoading = false" in finally_body
    assert '$("refresh-dashboard").disabled = false' in finally_body

    mobile = javascript_function_body(js, "setMobileNavigationOpen")
    assert "restoreFocus" in mobile
    assert "mobileNavigationMedia.matches" in mobile
    assert '.setAttribute("inert", "")' in mobile
    assert '.setAttribute("aria-hidden", "true")' in mobile
    assert '.removeAttribute("inert")' in mobile
    assert '.removeAttribute("aria-hidden")' in mobile
    assert '$("mobile-menu").focus()' in mobile

    operator = javascript_function_body(js, "setOperatorMenuOpen")
    assert "restoreFocus" in operator
    assert '$("operator-menu").focus()' in operator

    top_level_loads = re.findall(r"^loadDashboard\(\)\.catch\(handleDashboardError\);$", js, re.MULTILINE)
    top_level_live = re.findall(r"^connectLive\(\);$", js, re.MULTILINE)
    assert len(top_level_loads) == 1, "exactly one top-level dashboard startup"
    assert len(top_level_live) == 1, "exactly one top-level live startup"
    assert js.rstrip().endswith("loadDashboard().catch(handleDashboardError);\nconnectLive();")

    assert '$("refresh-dashboard").addEventListener("click", () => refreshDashboard().catch(handleDashboardError))' in js
    assert '$("mobile-menu").addEventListener("click", () => setMobileNavigationOpen(true))' in js
    assert '$("sidebar-close").addEventListener("click", () => setMobileNavigationOpen(false, true))' in js
    assert 'const mobileNavigationMedia = window.matchMedia("(max-width: 720px)")' in js
    assert js.count('window.matchMedia("(max-width: 720px)")') == 1
    assert 'mobileNavigationMedia.addEventListener("change", () => {' in js
    assert "setMobileNavigationOpen(state.mobileNavigationOpen);" in js
    assert 'if (state.operatorMenuOpen) setOperatorMenuOpen(false, true)' in js
    assert 'if (state.mobileNavigationOpen) setMobileNavigationOpen(false, true)' in js


def parse_html(html: str) -> Element:
    parser = LocalDOMParser()
    parser.feed(html)
    return parser.finish()


def elements_by_id(root: Element) -> dict[str, Element]:
    elements = root.descendants()
    ids = [element.attrs["id"] for element in elements if element.attrs.get("id")]
    assert len(ids) == len(set(ids)), "duplicate id"
    return {element.attrs["id"]: element for element in elements if element.attrs.get("id")}


def is_descendant(element: Element, ancestor: Element) -> bool:
    parent = element.parent
    while parent is not None:
        if parent is ancestor:
            return True
        parent = parent.parent
    return False


def control_type(element: Element) -> str | None:
    declared_type = element.attrs.get("type")
    if element.tag == "button":
        return (declared_type or "submit").casefold()
    if element.tag == "input":
        return (declared_type or "text").casefold()
    return declared_type.casefold() if declared_type is not None else None


def contenteditable_state(element: Element) -> str | None:
    declared = element.attrs.get("contenteditable")
    if "contenteditable" not in element.attrs:
        return None
    normalized = (declared or "").strip().casefold()
    return normalized if normalized in {"true", "plaintext-only"} else "true" if normalized == "" else None


def is_effectively_disabled(element: Element) -> bool:
    if "disabled" in element.attrs:
        return True
    parent = element.parent
    while parent is not None:
        if parent.tag == "fieldset" and "disabled" in parent.attrs:
            legends = [child for child in parent.children if child.tag == "legend"]
            if legends and (element is legends[0] or is_descendant(element, legends[0])):
                parent = parent.parent
                continue
            return True
        parent = parent.parent
    return False


def belongs_to_active_document(element: Element, root: Element) -> bool:
    current: Element | None = element
    while current is not None:
        if current.tag == "template":
            return False
        if current is root:
            return True
        current = current.parent
    return False


def has_inert_ancestry(element: Element) -> bool:
    current: Element | None = element
    while current is not None:
        if "inert" in current.attrs:
            return True
        current = current.parent
    return False


def has_ancestor(element: Element, tag: str) -> bool:
    parent = element.parent
    while parent is not None:
        if parent.tag == tag:
            return True
        parent = parent.parent
    return False


def is_interactive_surface(element: Element) -> bool:
    role = " ".join((element.attrs.get("role") or "").casefold().split())
    return (
        element.tag in FORM_ASSOCIATED_TAGS
        or (element.tag in {"a", "area"} and "href" in element.attrs)
        or element.tag == "summary"
        or element.tag in {"iframe", "embed"}
        or (element.tag in {"audio", "video"} and "controls" in element.attrs)
        or "tabindex" in element.attrs
        or contenteditable_state(element) is not None
        or bool(role)
    )


def semantic_interactive_inventory(
    root: Element,
) -> list[tuple[Element, Element | None, InteractiveContract]]:
    forms = root.descendants("form")
    interactive = [element for element in root.descendants() if is_interactive_surface(element)]
    inventory: list[tuple[Element, Element | None, InteractiveContract]] = []

    for element in interactive:
        owner: Element | None = None
        if element.tag in FORM_ASSOCIATED_TAGS:
            explicit_owner = element.attrs.get("form")
            if explicit_owner is not None:
                matching_forms = [form for form in forms if form.attrs.get("id") == explicit_owner]
                assert len(matching_forms) == 1, "effective form owner"
                owner = matching_forms[0]
            else:
                parent = element.parent
                while parent is not None:
                    if parent.tag == "form":
                        owner = parent
                        break
                    parent = parent.parent

        owner_contract = None
        if owner is not None:
            owner_contract = (
                "form",
                forms.index(owner),
                owner.attrs.get("id"),
                (owner.attrs.get("method") or "get").casefold(),
                owner.attrs.get("action") or "",
            )

        destination = None
        if element.tag in {"a", "area"}:
            destination = element.attrs.get("href")
        elif element.tag == "iframe" or element.tag == "embed" or element.tag in {"audio", "video"}:
            destination = element.attrs.get("src")
        elif element.tag == "object":
            destination = element.attrs.get("data")
        elif element.tag in {"button", "input"} and control_type(element) in {"submit", "image"}:
            destination = element.attrs.get("formaction")
            if destination is None and owner is not None:
                destination = owner.attrs.get("action") or ""

        submit_method = None
        if element.tag in {"button", "input"} and control_type(element) in {"submit", "image"}:
            submit_method = element.attrs.get("formmethod")
            if submit_method is None and owner is not None:
                submit_method = owner.attrs.get("method") or "get"
            submit_method = submit_method.casefold() if submit_method is not None else None

        stable_identity = None
        if element.tag == "summary":
            parent = element.parent
            descendant_id = next(
                (
                    descendant.attrs["id"]
                    for descendant in parent.descendants()
                    if descendant.attrs.get("id")
                ),
                None,
            ) if parent is not None else None
            stable_identity = (
                " ".join(element.text().split()),
                parent.tag if parent is not None else "#document",
                descendant_id,
            )

        role = element.attrs.get("role")
        tabindex = element.attrs.get("tabindex")
        active = belongs_to_active_document(element, root) and not has_inert_ancestry(element)
        enabled = not is_effectively_disabled(element)
        excluded_from_submission = has_ancestor(element, "datalist")
        actionable = (
            element.tag in {"button", "input"}
            and control_type(element) in {"submit", "image"}
            and active
            and enabled
            and "hidden" not in element.attrs
            and not excluded_from_submission
        )
        successful = (
            element.tag in FORM_ASSOCIATED_TAGS
            and owner is not None
            and active
            and enabled
            and not excluded_from_submission
            and bool(element.attrs.get("name"))
            and control_type(element) not in {"button", "reset", "submit", "image"}
        )
        contract = InteractiveContract(
            tag=element.tag,
            element_id=element.attrs.get("id"),
            effective_type=control_type(element),
            effective_destination=destination,
            effective_owner=owner_contract,
            effective_submit_method=submit_method,
            enabled=enabled,
            role=" ".join(role.casefold().split()) if role is not None else None,
            tabindex=tabindex.strip() if tabindex is not None else None,
            contenteditable=contenteditable_state(element),
            stable_identity=stable_identity,
            active=active,
            actionable=actionable,
            successful=successful,
        )
        inventory.append((element, owner, contract))

    return inventory


def assert_dashboard_dom_contract(html: str) -> None:
    root = parse_html(html)
    by_id = elements_by_id(root)
    inline_handlers = [
        (element, attribute)
        for element in root.descendants()
        for attribute in element.attrs
        if attribute.casefold().startswith("on")
    ]
    assert not inline_handlers, "inline event handler"
    interactive_inventory = semantic_interactive_inventory(root)
    controls = [element for element, _, _ in interactive_inventory]

    forms = root.descendants("form")
    assert len(forms) == 1
    logout_form = forms[0]
    assert belongs_to_active_document(logout_form, root), "logout form must belong to active document"
    assert not has_inert_ancestry(logout_form), "logout form must not have inert ancestry"
    operator_popover = by_id["operator-popover"]
    assert logout_form.parent is operator_popover, "logout form must be directly owned by operator popover"
    assert "hidden" not in logout_form.attrs, "logout form must remain visibly toggleable"
    assert "hidden" in operator_popover.attrs, "operator popover must start hidden"
    current: Element | None = logout_form
    while current is not None:
        if "hidden" in current.attrs:
            assert current is operator_popover, "only operator popover may hide logout ancestry"
        if current is root:
            break
        current = current.parent
    assert current is root, "logout ancestry must reach active document"
    assert (logout_form.attrs.get("method") or "get").casefold() == "post"
    assert logout_form.attrs.get("action") == "/ops/logout"
    assert "formaction" not in logout_form.attrs
    csrf = [element for element in controls if element.tag == "input" and element.attrs.get("name") == "csrf"]
    assert len(csrf) == 1
    csrf_record = next((owner, contract) for element, owner, contract in interactive_inventory if element is csrf[0])
    assert (
        csrf_record[0] is logout_form
        and csrf_record[1].enabled
        and csrf_record[1].active
        and csrf[0].attrs.get("type") == "hidden"
        and csrf[0].attrs.get("name") == "csrf"
        and csrf[0].attrs.get("value") == "{{CSRF}}"
    ), "logout CSRF semantics"
    assert csrf_record[1].successful, "logout CSRF must be successful"

    enabled_submits = [
        (element, owner, contract)
        for element, owner, contract in interactive_inventory
        if element.tag in {"button", "input"}
        and control_type(element) in {"submit", "image"}
        and contract.enabled
    ]
    assert len(enabled_submits) == 1, "logout submit semantics"
    submit, submit_owner, submit_contract = enabled_submits[0]
    assert submit_contract.active and submit_contract.actionable, "logout submit must be actionable"
    assert (
        submit_owner is logout_form
        and submit_contract.effective_submit_method == "post"
        and submit_contract.effective_destination == "/ops/logout"
    ), "logout submit semantics"
    assert submit.text().strip() == "Sair"
    assert not [element for element in controls if "formaction" in element.attrs]

    for element in by_id.values():
        parent = element.parent
        while parent is not None:
            assert parent.tag != "template", f"id={element.attrs['id']} is inert"
            parent = parent.parent

    control_contract = Counter(contract for _, _, contract in interactive_inventory)
    assert control_contract == Counter(EXPECTED_CONTROLS), "unexpected interactive element contract"
    assert set(by_id) == EXPECTED_IDS

    overview = by_id["overview-view"]
    drawer = by_id["execution-drawer"]
    canvas = by_id["execution-canvas"]
    assert overview.parent is not None and overview.parent.tag == "main"
    assert drawer.tag == "aside"
    assert drawer.attrs.get("aria-hidden") == "true", "drawer must start closed"
    assert "open" not in (drawer.attrs.get("class") or "").split(), "drawer must start closed"
    assert is_descendant(canvas, drawer)
    assert is_descendant(by_id["input-panel"], drawer)
    assert is_descendant(by_id["output-panel"], drawer)
    assert "hidden" in by_id["drawer-backdrop"].attrs

    input_panel = by_id["input-panel"]
    output_panel = by_id["output-panel"]
    inspectors = [
        element
        for element in root.descendants("aside")
        if "inspector" in (element.attrs.get("class") or "").split()
    ]
    assert len(inspectors) == 1, "exactly one aside.inspector"
    inspector = inspectors[0]
    execution_details = [
        element
        for element in root.descendants()
        if "execution-detail" in (element.attrs.get("class") or "").split()
    ]
    assert len(execution_details) == 1, "exactly one execution detail"
    execution_detail = execution_details[0]
    assert inspector.parent is execution_detail, "inspector must be directly under execution detail"
    assert is_descendant(execution_detail, drawer), "execution detail must be under drawer"
    assert input_panel.parent is inspector, "Input must be a direct child of inspector"
    assert output_panel.parent is inspector, "Output must be a direct child of inspector"

    for element in (inspector, input_panel, output_panel):
        current: Element | None = element
        while current is not None:
            assert current.tag != "template", "inspector chain must remain visible and active"
            assert "inert" not in current.attrs, "inspector chain must remain visible and active"
            assert "hidden" not in current.attrs, "inspector chain must remain visible and active"
            if current is drawer:
                break
            current = current.parent
        assert current is drawer, "inspector chain must remain under drawer"

    anonymous_form_controls = [
        element
        for element in controls
        if element.tag in FORM_ASSOCIATED_TAGS and not element.attrs.get("id")
    ]
    assert len(anonymous_form_controls) == 2
    assert set(map(id, anonymous_form_controls)) == {id(csrf[0]), id(submit)}

    links = root.descendants("link")
    scripts = [element.attrs.get("src") for element in root.descendants("script")]
    assert len(links) == 1
    assert (links[0].attrs.get("rel") or "").casefold() == "stylesheet"
    assert links[0].attrs.get("href") == "/ops/static/ops.css"
    assert scripts == ["/ops/static/ops.js"]


def test_dashboard_shell_dom_contract() -> None:
    html, _, _ = assets()
    assert_dashboard_dom_contract(html)
    assert "SOMENTE LEITURA" in html
    assert "Dados demonstrativos" not in html


@pytest.mark.parametrize(
    ("mutant", "message"),
    (
        (
            lambda html: html.replace(
                'aria-hidden="true" id="execution-drawer"',
                'aria-hidden="false" id="execution-drawer"',
            ),
            "drawer must start closed",
        ),
        (
            lambda html: html.replace(
                '<div id="drawer-summary"',
                '<button id="retry-execution" type="button">Retry</button><div id="drawer-summary"',
            ),
            "unexpected interactive element contract",
        ),
        (
            lambda html: html.replace(
                '<div id="drawer-summary"',
                '<textarea name="operator-note"></textarea><div id="drawer-summary"',
            ),
            "unexpected interactive element contract",
        ),
        (
            lambda html: html.replace(
                '<div id="drawer-summary"',
                '<a href="/ops/retry">Retry</a><div id="drawer-summary"',
            ),
            "unexpected interactive element contract",
        ),
        (
            lambda html: html.replace(
                '<input type="hidden" name="csrf"',
                '<input form="ghost" type="hidden" name="csrf"',
            ),
            "effective form owner",
        ),
        (
            lambda html: html.replace(
                '<button type="submit">Sair</button>',
                '<button form="ghost" type="submit">Sair</button>',
            ),
            "effective form owner",
        ),
        (
            lambda html: html.replace(
                '<button type="submit">Sair</button>',
                '<button type="submit" formmethod="get">Sair</button>',
            ),
            "logout submit semantics",
        ),
        (
            lambda html: html.replace(
                '<input type="hidden" name="csrf"',
                '<input disabled type="hidden" name="csrf"',
            ),
            "logout CSRF semantics",
        ),
        (
            lambda html: html.replace(
                '<button type="submit">Sair</button>',
                '<button disabled type="submit">Sair</button>',
            ),
            "logout submit semantics",
        ),
        (
            lambda html: html.replace(
                '<div id="drawer-summary"',
                '<details><summary>Ação extra</summary>extra</details><div id="drawer-summary"',
            ),
            "unexpected interactive element contract",
        ),
        (
            lambda html: html.replace(
                '<div id="drawer-summary"',
                '<map name="extra"><area href="/ops/retry" alt="Retry"></map><div id="drawer-summary"',
            ),
            "unexpected interactive element contract",
        ),
        (
            lambda html: html.replace(
                '<div id="drawer-summary"',
                '<div role="button" tabindex="0">Ação extra</div><div id="drawer-summary"',
            ),
            "unexpected interactive element contract",
        ),
        (
            lambda html: html.replace(
                '<div id="drawer-summary"',
                '<div contenteditable="true">Ação extra</div><div id="drawer-summary"',
            ),
            "unexpected interactive element contract",
        ),
        pytest.param(
            lambda html: html.replace(
                '<button type="submit">Sair</button>',
                '<button type="submit" hidden>Sair</button>',
            ),
            "logout submit must be actionable",
            id="logout-submit-hidden",
        ),
        pytest.param(
            lambda html: html.replace(
                '<form method="post" action="/ops/logout">',
                '<template><form method="post" action="/ops/logout">',
            ).replace(
                '</button></form>',
                '</button></form></template>',
                1,
            ),
            "logout form must belong to active document",
            id="logout-form-under-template",
        ),
        pytest.param(
            lambda html: html.replace(
                '<div id="operator-popover" class="operator-popover" hidden>',
                '<div id="operator-popover" class="operator-popover" hidden inert>',
            ),
            "logout form must not have inert ancestry",
            id="logout-ancestor-inert",
        ),
        pytest.param(
            lambda html: html.replace(
                '<input type="hidden" name="csrf" value="{{CSRF}}"><button type="submit">Sair</button>',
                '<datalist><input type="hidden" name="csrf" value="{{CSRF}}"><button type="submit">Sair</button></datalist>',
            ),
            "logout CSRF must be successful",
            id="logout-controls-under-datalist",
        ),
        pytest.param(
            lambda html: html.replace(
                '<div id="drawer-summary"',
                '<div role="searchbox">Busca extra</div><div id="drawer-summary"',
            ),
            "unexpected interactive element contract",
            id="unauthorized-role-searchbox",
        ),
        pytest.param(
            lambda html: html.replace(
                '<div id="drawer-summary"',
                '<div role="scrollbar">Rolagem extra</div><div id="drawer-summary"',
            ),
            "unexpected interactive element contract",
            id="unauthorized-role-scrollbar",
        ),
        pytest.param(
            lambda html: html.replace(
                '<div id="drawer-summary"',
                '<div role="menu">Menu extra</div><div id="drawer-summary"',
            ),
            "unexpected interactive element contract",
            id="unauthorized-composite-role-menu",
        ),
        pytest.param(
            lambda html: html.replace(
                '<div id="drawer-summary"',
                '<div onclick="retry()">Retry</div><div id="drawer-summary"',
            ),
            "inline event handler",
            id="unauthorized-inline-onclick",
        ),
        pytest.param(
            lambda html: html.replace(
                '<details><summary>Metadados técnicos</summary><pre id="metadata">{}</pre></details>\n'
                '            <details><summary>Erro sanitizado</summary><pre id="node-error">null</pre></details>',
                '<details><summary>Erro sanitizado</summary><pre id="metadata">{}</pre></details>\n'
                '            <details><summary>Metadados técnicos</summary><pre id="node-error">null</pre></details>',
            ),
            "unexpected interactive element contract",
            id="swapped-summary-disclosure-bindings",
        ),
        pytest.param(
            lambda html: html.replace(
                '<div id="operator-popover" class="operator-popover" hidden>',
                '<div id="operator-popover" class="operator-popover">',
                1,
            ),
            "operator popover must start hidden",
            id="logout-operator-popover-not-hidden",
        ),
        pytest.param(
            lambda html: html.replace(
                '<div class="header-actions">',
                '<div class="header-actions" hidden>',
                1,
            ),
            "only operator popover may hide logout ancestry",
            id="logout-outer-ancestor-hidden",
        ),
        pytest.param(
            lambda html: html.replace(
                '<form method="post" action="/ops/logout">',
                '<form hidden method="post" action="/ops/logout">',
                1,
            ),
            "logout form must remain visibly toggleable",
            id="logout-form-hidden",
        ),
        pytest.param(
            lambda html: html.replace(
                '<form method="post" action="/ops/logout">',
                '<div hidden><form method="post" action="/ops/logout">',
                1,
            ).replace(
                '</button></form>',
                '</button></form></div>',
                1,
            ),
            "logout form must be directly owned by operator popover",
            id="logout-hidden-intermediate-wrapper",
        ),
        pytest.param(
            lambda html: html.replace(
                '<aside class="inspector">',
                '<aside class="inspector" hidden>',
                1,
            ),
            "inspector chain must remain visible and active",
            id="inspector-hidden",
        ),
        pytest.param(
            lambda html: html.replace(
                '            <section id="input-panel" class="io-panel"><div class="panel-head"><strong>Input</strong><button id="load-full-input" type="button" hidden>Carregar permitido</button></div><pre id="input-summary">{}</pre><pre id="input-full" hidden></pre></section>\n',
                '',
                1,
            ).replace(
                '          </aside>\n        </div>',
                '          </aside>\n'
                '          <section id="input-panel" class="io-panel"><div class="panel-head"><strong>Input</strong><button id="load-full-input" type="button" hidden>Carregar permitido</button></div><pre id="input-summary">{}</pre><pre id="input-full" hidden></pre></section>\n'
                '        </div>',
                1,
            ),
            "Input must be a direct child of inspector",
            id="input-panel-moved-outside-inspector",
        ),
        pytest.param(
            lambda html: html.replace(
                '            <section id="output-panel" class="io-panel"><div class="panel-head"><strong>Output</strong><button id="load-full-output" type="button" hidden>Carregar permitido</button></div><pre id="output-summary">{}</pre><pre id="output-full" hidden></pre></section>\n',
                '',
                1,
            ).replace(
                '          </aside>\n        </div>',
                '          </aside>\n'
                '          <section id="output-panel" class="io-panel"><div class="panel-head"><strong>Output</strong><button id="load-full-output" type="button" hidden>Carregar permitido</button></div><pre id="output-summary">{}</pre><pre id="output-full" hidden></pre></section>\n'
                '        </div>',
                1,
            ),
            "Output must be a direct child of inspector",
            id="output-panel-moved-outside-inspector",
        ),
        (
            lambda html: html.replace(
                '    <aside class="execution-drawer" aria-hidden="true" id="execution-drawer"',
                '    <template><aside class="execution-drawer" aria-hidden="true" id="execution-drawer"',
                1,
            ).replace(
                '    </aside>\n  </div>\n  <script',
                '    </aside></template>\n  </div>\n  <script',
                1,
            ),
            "is inert",
        ),
    ),
)
def test_dom_contract_rejects_causal_mutants(
    mutant: Callable[[str], str], message: str
) -> None:
    html, _, _ = assets()
    mutated = mutant(html)
    assert mutated != html, f"mutant fixture did not apply: {message}"
    with pytest.raises(AssertionError, match=message):
        assert_dashboard_dom_contract(mutated)


def test_accessibility_metadata_is_explicit() -> None:
    html, _, _ = assets()
    root = parse_html(html)
    by_id = elements_by_id(root)

    table = root.descendants("table")
    assert len(table) == 1
    captions = table[0].descendants("caption")
    assert len(captions) == 1 and captions[0].text().strip()
    headers = table[0].descendants("th")
    assert headers and all(header.attrs.get("scope") == "col" for header in headers)

    canvas = by_id["execution-canvas"]
    assert canvas.attrs.get("role") == "region"
    assert canvas.attrs.get("aria-label") == "Canvas da execução"
    assert by_id["zoom-out"].attrs.get("aria-label") == "Reduzir zoom"
    assert by_id["zoom-in"].attrs.get("aria-label") == "Aumentar zoom"


def test_html_has_no_commercial_claims() -> None:
    html, _, _ = assets()
    lowered = html.casefold()
    for forbidden in (
        "receita",
        "conversão",
        "novos leads",
        "qualificados",
        "interesse",
        "reservas confirmadas",
        "pagamento pago",
        "retry",
        "replay",
    ):
        assert forbidden not in lowered


def test_mockup_fidelity_header_and_kpi_contract() -> None:
    html, js, css = assets()
    assert_task2_javascript_contract(js)
    for token in (
        "brand-lockup", "readonly-chip", "welcome-row", "health-pill",
        "header-actions", "refresh-dashboard", "operator-popover",
    ):
        assert token in html
    for function_name in ("refreshDashboard", "setMobileNavigationOpen"):
        assert f"function {function_name}(" in js or f"async function {function_name}(" in js
    for token in ("kpi-top", "kpi-icon", "kpi-label", "kpi-value", "kpi-foot", "sparkline"):
        assert token in js
    assert 'setAttribute("aria-expanded"' in js
    assert "execution_series" in js
    assert "previous period" not in js.casefold()
    assert "período anterior" not in (html + js).casefold()
    for selector in (".brand-lockup", ".welcome-row", ".health-pill", ".kpi-icon", ".sparkline"):
        assert selector in css


def test_mockup_fidelity_analytics_are_closed_to_real_payload() -> None:
    html, js, css = assets()
    for title in (
        "Execuções no período", "Estados atuais", "Completude do trace",
        "Marcos registrados", "Nós registrados",
    ):
        assert title in html
    for function_name in (
        "renderStatusDistribution", "renderTraceDistribution",
        "renderMilestones", "renderTopNodeTypes",
    ):
        assert f"function {function_name}(" in js
    for token in ("chart-area", "chart-grid", "chart-point", "donut", "chart-legend", "bar-chart"):
        assert token in js or f".{token}" in css

    # The canonical desktop composition is exactly 2 + 3 panels over six columns.
    for panel_class in (
        "analytics-panel panel-volume", "analytics-panel panel-status",
        "analytics-panel panel-trace", "analytics-panel panel-milestones",
        "analytics-panel panel-nodes",
    ):
        assert panel_class in html
    compact_css = re.sub(r"\s+", "", css)
    assert ".analytics-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));" in compact_css
    assert ".panel-volume,.panel-status{grid-column:span3;}" in compact_css
    assert ".panel-trace,.panel-milestones,.panel-nodes{grid-column:span2;}" in compact_css
    assert ".analytics-panel.wide" not in css
    tablet_css = compact_css.split("@media(max-width:1100px)", 1)[1].split("@media(max-width:720px)", 1)[0]
    assert ".analytics-panel{min-height:0" in tablet_css
    assert ".analytics-panel{min-height:0" in compact_css.split("@media(max-width:720px)", 1)[1]

    closed_status = javascript_function_body(js, "closedStatusDistribution")
    status_renderer = javascript_function_body(js, "renderStatusDistribution")
    assert "Object.prototype.hasOwnProperty.call(STATUS_PRESENTATION" in closed_status
    assert "const items = closedStatusDistribution()" in status_renderer
    combined = (html + js).casefold()
    for forbidden in ("funil", "receita", "conversão", "interesse", "motivo de handoff"):
        assert forbidden not in combined


def test_approved_visual_palette_and_responsive_structure() -> None:
    _, _, css = assets()
    for color in (
        "#245634",
        "#173a27",
        "#f4e8d7",
        "#fffcf7",
        "#e4d5c0",
        "#66766b",
        "#e85f67",
    ):
        assert color in css.casefold()
    assert css.count("@media") >= 2
    for token in (
        ".kpi-grid",
        ".analytics-grid",
        ".operations-panel",
        ".execution-detail",
        ".execution-mobile-list",
    ):
        assert token in css
    assert "max-width:1100px" in css.replace(" ", "")
    assert "max-width:720px" in css.replace(" ", "")
    assert ":focus-visible" in css
    assert "prefers-reduced-motion" in css


def test_existing_javascript_and_css_read_only_markers_are_preserved() -> None:
    html, js, css = assets()
    assert "EventSource" in js
    assert "textContent" in js
    assert "innerHTML" not in js
    assert "fetch(" in js
    assert "method:" not in js
    for forbidden in (
        "reserve",
        "charge",
        "payment link",
        "retry",
        "replay",
        "send message",
    ):
        assert forbidden not in (html + js).casefold()
    assert "@media" in css
    assert "input-panel" in css and "output-panel" in css


def test_javascript_dashboard_contract_is_safe_and_explicit() -> None:
    _, js, _ = assets()
    for name in (
        "loadDashboard",
        "renderKpis",
        "renderExecutionSeries",
        "renderStatusDistribution",
        "renderTraceDistribution",
        "renderMilestones",
        "renderTopNodeTypes",
        "renderExecutionTable",
        "filteredExecutions",
        "openExecution",
        "showOverview",
        "renderCanvas",
        "selectNode",
        "connectLive",
    ):
        assert f"function {name}(" in js or f"async function {name}(" in js

    assert "textContent" in js
    assert "document.createElement(" in js
    assert "document.createElementNS(" in js
    assert "replaceChildren" in js
    assert "setAttribute" in js
    assert "innerHTML" not in js
    assert "method:" not in js
    for forbidden in (
        "SERVICES",
        "RANGE_MODELS",
        "demo-",
        "payment_link",
        "request_handoff",
        "create_reservation",
    ):
        assert forbidden not in js


def test_javascript_uses_the_closed_state_cards_and_exact_filters() -> None:
    _, js, _ = assets()
    for field, initial in (
        ("range", '"7d"'),
        ("snapshot", "null"),
        ("selectedExecution", "null"),
        ("nodes", "[]"),
        ("selectedNode", "null"),
        ("statusFilter", '""'),
        ("completenessFilter", '""'),
        ("leadFilter", '""'),
        ("zoom", "1"),
        ("connected", "false"),
        ("mobileNavigationOpen", "false"),
        ("operatorMenuOpen", "false"),
        ("dashboardLoading", "false"),
        ("drawerOpen", "false"),
        ("detailTrigger", "null"),
    ):
        assert f"{field}: {initial}" in js

    for definition in (
        '["executions", "Execuções", "◎", "Eventos recebidos no período", "neutral"]',
        '["distinct_leads", "Leads distintos", "◇", "IDs distintos no período", "neutral"]',
        '["in_progress", "Em andamento", "◌", "Pending, running e stale", "active"]',
        '["completed", "Concluídas", "✓", "Conclusão técnica", "success"]',
        '["failed", "Falhas", "!", "Estado técnico failed", "danger"]',
        '["manual_review", "Revisão manual", "↗", "Estado manual_review", "warning"]',
        '["technical_completion_rate", "Conclusão técnica", "%", "Concluídas sobre execuções", "success"]',
        '["average_terminal_duration_ms", "Duração média terminal", "◷", "Apenas execuções terminais", "neutral"]',
    ):
        assert definition in js

    assert "execution.lead_id === state.leadFilter" in js
    assert "execution.status === state.statusFilter" in js
    assert "execution.trace_completeness === state.completenessFilter" in js


def test_javascript_preserves_detail_and_uses_sse_without_polling() -> None:
    _, js, _ = assets()
    assert "const requestedRange = state.range" in js
    assert 'getJSON(`/ops/api/dashboard?range=${encodeURIComponent(requestedRange)}`)' in js
    assert "epoch !== state.dashboardEpoch || requestedRange !== state.range" in js
    assert 'window.location.assign("/ops/login")' in js
    assert 'error.code = "source_unavailable"' in js
    assert 'new EventSource("/ops/api/events")' in js
    for event in ("ready", "change", "degraded"):
        assert f'addEventListener("{event}"' in js
    assert "setInterval" not in js
    assert "state.snapshot =" in js
    assert '$("execution-drawer").classList.add("open")' in js
    assert '$("execution-drawer").setAttribute("aria-hidden", "false")' in js
    assert '$("execution-drawer").classList.remove("open")' in js
    assert '$("execution-drawer").setAttribute("aria-hidden", "true")' in js
    assert 'payload.value ?? { status: "not_recorded" }' in js
    for token in (
        "lead_id",
        "execution_id",
        "received_at",
        "duration_ms",
        "status",
        "trace_completeness",
        "current_node_type",
        "node_count",
        "has_reservation",
        "has_payment",
        "has_public_delivery",
        "has_handoff",
        "terminal_reason",
    ):
        assert token in js


BROWSER_SPEC = r"""
const { test, expect } = require('@playwright/test');
const fs = require('fs');
const html = fs.readFileSync('/static/index.html', 'utf8')
  .replace('  <script src="/ops/static/ops.js" defer></script>\n', '');
const js = fs.readFileSync('/static/ops.js', 'utf8');

function snapshot(label, executions, executionSeries = []) {
  return {
    generated_at: '2026-08-21T12:00:00Z', range: label,
    metrics: {executions, distinct_leads: executions, in_progress: 0,
      completed: executions, failed: 0, manual_review: 0,
      technical_completion_rate: 100, average_terminal_duration_ms: 1000},
    execution_series: executionSeries,
    status_distribution: [
      {status: 'completed', count: Math.max(0, executions - 1)},
      {status: 'running', count: executions ? 1 : 0},
      {status: 'failed', count: 0},
    ],
    trace_distribution: [
      {trace_completeness: 'complete_trace', count: executions},
      {trace_completeness: 'partial_trace', count: 0},
      {trace_completeness: 'ledger_only', count: 0},
    ],
    milestones: [
      {milestone: 'reservation', count: 1}, {milestone: 'payment', count: 1},
      {milestone: 'public_delivery', count: 0}, {milestone: 'handoff', count: 0},
    ],
    top_node_types: [{node_type: 'maya_request', count: executions}], executions: [],
  };
}

function nodes(executionId) {
  return {nodes: [{
    node_id: `node-${executionId}`, node_type: `type_${executionId}`,
    ordinal: 1, attempt: 1, status: 'completed',
    input_summary: {execution: executionId}, output_summary: {execution: executionId},
    technical_metadata: {execution: executionId}, error: null,
    has_full_input: true, has_full_output: true,
  }]};
}

async function setup(page) {
  await page.setContent(html);
  await page.evaluate(() => {
    window.__requests = [];
    window.__eventSources = [];
    window.fetch = (path, options) => new Promise((resolve, reject) => {
      window.__requests.push({path: String(path), options, resolve, reject});
    });
    window.__resolveRequest = (index, status, payload) => {
      window.__requests[index].resolve({
        status, ok: status >= 200 && status < 300,
        json: async () => payload,
      });
    };
    window.__rejectRequest = (index, message) => {
      window.__requests[index].reject(new Error(message));
    };
    class DeterministicEventSource {
      constructor(path) {
        this.path = path;
        this.listeners = {};
        window.__eventSources.push(this);
        window.__eventSource = this;
      }
      addEventListener(name, callback) { this.listeners[name] = callback; }
      emit(name) { this.listeners[name](); }
      fail() { this.onerror(new Error('disconnected')); }
    }
    window.EventSource = DeterministicEventSource;
  });
  await page.addScriptTag({content: js});
}

async function resolveRequest(page, index, status, payload) {
  await page.evaluate(
    ([index, status, payload]) => window.__resolveRequest(index, status, payload),
    [index, status, payload],
  );
  await page.waitForTimeout(0);
}

async function paths(page) {
  return page.evaluate(() => window.__requests.map(request => request.path));
}

test('Task 2 startup creates exactly one initial load and one EventSource', async ({page}) => {
  await setup(page);
  expect(await paths(page)).toEqual(['/ops/api/dashboard?range=7d']);
  expect(await page.evaluate(() => window.__eventSources.map(source => source.path)))
    .toEqual(['/ops/api/events']);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  await page.waitForTimeout(0);
  expect((await paths(page)).length).toBe(1);
  expect(await page.evaluate(() => window.__eventSources.length)).toBe(1);
});

test('Task 2 refresh is deduplicated, disabled while pending, and released on success and error', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  await page.click('#refresh-dashboard');
  await page.evaluate(() => document.querySelector('#refresh-dashboard').click());
  expect((await paths(page)).length).toBe(2);
  await expect(page.locator('#refresh-dashboard')).toBeDisabled();
  await resolveRequest(page, 1, 200, snapshot('refreshed', 2));
  await expect(page.locator('#refresh-dashboard')).toBeEnabled();
  await page.click('#refresh-dashboard');
  expect((await paths(page)).length).toBe(3);
  await expect(page.locator('#refresh-dashboard')).toBeDisabled();
  await resolveRequest(page, 2, 503, {status: 'source_unavailable'});
  await expect(page.locator('#refresh-dashboard')).toBeEnabled();
});

test('Task 2 operator popover closes outside and restores trigger focus on Escape', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  await page.click('#operator-menu');
  await expect(page.locator('#operator-popover')).toBeVisible();
  await page.evaluate(() => document.body.dispatchEvent(new MouseEvent('click', {bubbles: true})));
  await expect(page.locator('#operator-popover')).toBeHidden();
  await expect(page.locator('#operator-menu')).toBeFocused();
  await page.click('#operator-menu');
  await page.locator('#operator-popover button[type="submit"]').focus();
  await page.keyboard.press('Escape');
  await expect(page.locator('#operator-popover')).toBeHidden();
  await expect(page.locator('#operator-menu')).toBeFocused();
  await expect(page.locator('#operator-menu')).toHaveAttribute('aria-expanded', 'false');
});

test('Task 2 closed sidebar synchronizes inert state across desktop and mobile resizes without stealing focus', async ({page}) => {
  await page.setViewportSize({width: 1280, height: 800});
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  const sidebar = page.locator('#app-sidebar');
  await expect(sidebar).not.toHaveClass(/mobile-open/);
  await expect(sidebar).not.toHaveAttribute('inert', '');
  await expect(sidebar).not.toHaveAttribute('aria-hidden', 'true');
  await page.locator('#refresh-dashboard').focus();
  await expect(page.locator('#refresh-dashboard')).toBeFocused();

  await page.setViewportSize({width: 390, height: 844});
  await expect(sidebar).not.toHaveClass(/mobile-open/);
  await expect(sidebar).toHaveAttribute('inert', '');
  await expect(sidebar).toHaveAttribute('aria-hidden', 'true');
  await expect(page.locator('#refresh-dashboard')).toBeFocused();

  await page.setViewportSize({width: 1280, height: 800});
  await expect(sidebar).not.toHaveClass(/mobile-open/);
  await expect(sidebar).not.toHaveAttribute('inert', '');
  await expect(sidebar).not.toHaveAttribute('aria-hidden', 'true');
  await expect(page.locator('#refresh-dashboard')).toBeFocused();
});

test('Task 2 mobile navigation is inert when closed and restores trigger focus', async ({page}) => {
  await page.setViewportSize({width: 390, height: 844});
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  const sidebar = page.locator('#app-sidebar');
  await expect(sidebar).toHaveAttribute('inert', '');
  await expect(sidebar).toHaveAttribute('aria-hidden', 'true');
  await page.click('#mobile-menu');
  await expect(sidebar).toHaveClass(/mobile-open/);
  await expect(sidebar).not.toHaveAttribute('inert', '');
  await expect(sidebar).not.toHaveAttribute('aria-hidden', 'true');
  await page.locator('#sidebar-close').focus();
  await page.keyboard.press('Escape');
  await expect(sidebar).not.toHaveClass(/mobile-open/);
  await expect(sidebar).toHaveAttribute('inert', '');
  await expect(sidebar).toHaveAttribute('aria-hidden', 'true');
  await expect(page.locator('#mobile-menu')).toBeFocused();
  expect(await page.evaluate(() => {
    document.querySelector('#sidebar-close').focus();
    return document.activeElement.id;
  })).toBe('mobile-menu');
});

test('Task 2 renders exactly eight safe KPI components from execution_series points', async ({page}) => {
  await setup(page);
  const payload = snapshot('initial', 3, [{count: 1}, {count: 3}]);
  payload.metrics.executions = '<img src=x onerror=alert(1)>';
  payload.kpi_series = [{count: 99}];
  await resolveRequest(page, 0, 200, payload);
  const cards = page.locator('#kpi-grid > .kpi-card');
  await expect(cards).toHaveCount(8);
  for (let index = 0; index < 8; index += 1) {
    const card = cards.nth(index);
    await expect(card.locator(':scope > .kpi-top')).toHaveCount(1);
    await expect(card.locator(':scope > .kpi-top > .kpi-icon')).toHaveCount(1);
    await expect(card.locator(':scope > .kpi-top > .kpi-label')).toHaveCount(1);
    await expect(card.locator(':scope > .kpi-value')).toHaveCount(1);
    await expect(card.locator(':scope > .kpi-foot')).toHaveCount(1);
    await expect(card.locator('.kpi-series')).toContainText('Execuções no período');
  }
  await expect(page.locator('#kpi-grid img')).toHaveCount(0);
  await expect(cards.first().locator('.kpi-value')).toHaveText('<img src=x onerror=alert(1)>');
  await expect(page.locator('#kpi-grid .sparkline')).toHaveCount(8);
  await expect(page.locator('#kpi-grid .sparkline polyline').first())
    .toHaveAttribute('points', '0.00,14.67 66.00,2.00');
});

test('Task 2 omits empty sparklines but retains the factual execution note', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('empty', 0, []));
  await expect(page.locator('#kpi-grid > .kpi-card')).toHaveCount(8);
  await expect(page.locator('#kpi-grid .sparkline')).toHaveCount(0);
  await expect(page.locator('#kpi-grid .kpi-series > span')).toHaveCount(8);
  await expect(page.locator('#kpi-grid .kpi-series > span').first()).toHaveText('Execuções no período');
});

test('Task 3 causally renders five exact analytics sources and adversarial structures', async ({page}) => {
  await setup(page);
  const exclusive = snapshot('analytics-exclusive', 12, [
    {start_at: '2026-08-21T10:00:00Z', count: 17},
    {start_at: '2026-08-21T11:00:00Z', count: 29},
  ]);
  exclusive.status_distribution = [
    {status: 'completed', count: 7},
    {status: 'running', count: 5},
    {status: 'future_status_101', count: 101},
  ];
  exclusive.trace_distribution = [
    {trace_completeness: 'complete_trace', count: 13},
    {trace_completeness: 'partial_trace', count: 0},
  ];
  exclusive.milestones = [
    {milestone: 'payment', count: 11},
    {milestone: 'handoff', count: -23},
  ];
  exclusive.top_node_types = [
    {node_type: 'exclusive_node_19', count: 19},
    {node_type: 'nan_node_31', count: null},
  ];
  await resolveRequest(page, 0, 200, exclusive);

  const svg = page.locator('#execution-series svg[role="img"]');
  await expect(svg).toHaveCount(1);
  await expect(svg).toHaveAttribute('aria-label', 'Execuções registradas ao longo do período');
  await expect(svg.locator(':scope > .chart-grid')).toHaveCount(1);
  await expect(svg.locator(':scope > .chart-grid > line')).toHaveCount(5);
  await expect(svg.locator(':scope > path.chart-area')).toHaveCount(1);
  await expect(svg.locator(':scope > polyline.chart-line')).toHaveCount(1);
  await expect(svg.locator(':scope > circle.chart-point')).toHaveCount(2);
  await expect(svg.locator('circle.chart-point > title')).toHaveCount(2);
  await expect(svg.locator('circle.chart-point > title').nth(0)).toContainText(': 17');
  await expect(svg.locator('circle.chart-point > title').nth(1)).toContainText(': 29');
  await expect(svg.locator('polyline.chart-line')).toHaveAttribute(
    'points', '32.00,96.55 608.00,32.00',
  );
  expect(await page.locator('#execution-series').evaluate(root => {
    const values = [...root.querySelectorAll('[points], [d], circle')]
      .flatMap(node => [...node.attributes].map(attribute => attribute.value));
    return values.every(value => !/NaN|Infinity/.test(value));
  })).toBe(true);

  await expect(page.locator('#status-distribution .donut-center')).toHaveText('12');
  await expect(page.locator('#status-distribution .chart-legend')).toContainText('Concluída');
  await expect(page.locator('#status-distribution .chart-legend')).toContainText('Em execução');
  await expect(page.locator('#status-distribution')).not.toContainText('future_status_101');
  expect(await page.locator('#status-distribution .donut').evaluate(element =>
    element.style.getPropertyValue('--donut-segments'))).toBe(
      'var(--success-500) 0.00% 58.33%, var(--info-500) 58.33% 100.00%',
    );
  await expect(page.locator('#trace-distribution')).toContainText('13');
  await expect(page.locator('#trace-distribution')).not.toContainText('17');
  await expect(page.locator('#milestones-chart')).toContainText('11 execuções com marco');
  await expect(page.locator('#milestones-chart')).not.toContainText('13');
  await expect(page.locator('#top-node-types')).toContainText('exclusive node 19');
  await expect(page.locator('#top-node-types')).toContainText('19');
  expect(await page.locator('#trace-distribution .bar-value').evaluateAll(elements =>
    elements.map(element => element.style.width))).toEqual(['100%', '0%']);
  expect(await page.locator('#milestones-chart .bar-value').evaluateAll(elements =>
    elements.map(element => element.style.width))).toEqual(['100%', '0%']);
  expect(await page.locator('#top-node-types .bar-value').evaluateAll(elements =>
    elements.map(element => element.style.width))).toEqual(['100%', '0%']);

  const adversarial = snapshot('adversarial', 4, [
    {start_at: '2026-08-21T12:00:00Z', count: 5},
  ]);
  adversarial.status_distribution = [
    null, 42, {status: 'future_status_999', count: 999},
    {status: 'completed', count: null}, {status: 'running', count: -8},
    {status: 'failed', count: 4},
  ];
  adversarial.trace_distribution = [{trace_completeness: 'ledger_only', count: 23}];
  adversarial.milestones = [{milestone: 'reservation', count: 31}];
  adversarial.top_node_types = [{node_type: 'continuity_node_37', count: 37}];
  await page.evaluate(payload => {
    payload.status_distribution[3].count = Number.NaN;
    state.snapshot = payload;
    renderDashboard();
  }, adversarial);
  await expect(page.locator('#status-distribution .donut-center')).toHaveText('4');
  await expect(page.locator('#status-distribution .chart-legend .legend-row')).toHaveCount(3);
  await expect(page.locator('#status-distribution')).not.toContainText('future_status_999');
  await expect(page.locator('#trace-distribution')).toContainText('23');
  await expect(page.locator('#milestones-chart')).toContainText('31 execuções com marco');
  await expect(page.locator('#top-node-types')).toContainText('continuity node 37');

  const unitSvg = page.locator('#execution-series svg[role="img"]');
  await expect(unitSvg.locator('circle.chart-point')).toHaveAttribute('cx', '320.00');
  await expect(unitSvg.locator('polyline.chart-line')).toHaveAttribute('points', '320.00,32.00');
  await expect(unitSvg.locator('path.chart-area')).toHaveAttribute(
    'd', 'M 320.00 188 L 320.00,32.00 L 320.00 188 Z',
  );

  const zeroCatalogs = snapshot('zero-catalogs', 0, []);
  zeroCatalogs.status_distribution = [
    {status: 'completed', count: null}, {status: 'running', count: -2},
  ];
  zeroCatalogs.trace_distribution = [
    {trace_completeness: 'complete_trace', count: null},
    {trace_completeness: 'partial_trace', count: -3},
  ];
  zeroCatalogs.milestones = [{milestone: 'public_delivery', count: null}];
  zeroCatalogs.top_node_types = [{node_type: 'zero_node', count: -9}];
  await page.evaluate(payload => {
    payload.status_distribution[0].count = Number.NaN;
    payload.trace_distribution[0].count = Number.NaN;
    payload.milestones[0].count = Number.NaN;
    state.snapshot = payload;
    renderDashboard();
  }, zeroCatalogs);
  await expect(page.locator('#execution-series .empty-chart')).toHaveCount(1);
  await expect(page.locator('#status-distribution .donut')).toHaveCount(0);
  await expect(page.locator('#status-distribution .empty-chart')).toHaveCount(1);
  await expect(page.locator('#trace-distribution .bar-row')).toHaveCount(2);
  await expect(page.locator('#milestones-chart .bar-row')).toHaveCount(1);
  await expect(page.locator('#top-node-types .bar-row')).toHaveCount(1);
  expect(await page.locator('.bar-value').evaluateAll(elements =>
    elements.map(element => element.style.width))).toEqual(['0%', '0%', '0%', '0%']);

  const emptyArrays = snapshot('empty-arrays', 0, []);
  emptyArrays.status_distribution = [];
  emptyArrays.trace_distribution = [];
  emptyArrays.milestones = [];
  emptyArrays.top_node_types = [];
  await page.evaluate(payload => { state.snapshot = payload; renderDashboard(); }, emptyArrays);
  for (const root of [
    '#execution-series', '#status-distribution', '#trace-distribution',
    '#milestones-chart', '#top-node-types',
  ]) {
    await expect(page.locator(`${root} > .empty-chart`)).toHaveCount(1);
  }
});

test('dashboard ignores inverted A/B ranges', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  await page.selectOption('#range-select', '24h');
  await page.selectOption('#range-select', '30d');
  expect((await paths(page)).slice(1)).toEqual([
    '/ops/api/dashboard?range=24h', '/ops/api/dashboard?range=30d',
  ]);
  await resolveRequest(page, 2, 200, snapshot('manual-30d', 30));
  await resolveRequest(page, 1, 200, snapshot('stale-24h', 24));
  await expect(page.locator('#range-select')).toHaveValue('30d');
  await expect(page.locator('#kpi-grid strong').first()).toHaveText('30');
});

test('dashboard ignores an SSE load superseded by a manual range', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  await page.evaluate(() => window.__eventSource.emit('change'));
  await page.selectOption('#range-select', '24h');
  await resolveRequest(page, 2, 200, snapshot('manual-24h', 240));
  await resolveRequest(page, 1, 200, snapshot('stale-sse-7d', 7));
  await expect(page.locator('#range-select')).toHaveValue('24h');
  await expect(page.locator('#kpi-grid strong').first()).toHaveText('240');
});

test('superseded dashboard errors cannot overwrite current range or alert', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  await page.selectOption('#range-select', '24h');
  await page.selectOption('#range-select', '30d');
  await resolveRequest(page, 2, 200, snapshot('manual-30d', 30));
  await resolveRequest(page, 1, 503, {status: 'source_unavailable'});
  await expect(page.locator('#kpi-grid strong').first()).toHaveText('30');
  await expect(page.locator('#dashboard-alert')).toBeHidden();

  await page.evaluate(() => window.__eventSource.emit('change'));
  await page.selectOption('#range-select', '7d');
  await resolveRequest(page, 4, 200, snapshot('manual-7d', 70));
  await page.evaluate(() => window.__rejectRequest(3, 'obsolete network failure'));
  await expect(page.locator('#kpi-grid strong').first()).toHaveText('70');
  await expect(page.locator('#dashboard-alert')).toBeHidden();
});

test('detail commits inverted A/B responses atomically', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  await page.evaluate(() => { openExecution('A').catch(handleDashboardError); });
  await page.evaluate(() => { openExecution('B').catch(handleDashboardError); });
  await resolveRequest(page, 2, 200, nodes('B'));
  await resolveRequest(page, 1, 200, nodes('A'));
  await expect(page.locator('#canvas-title')).toHaveText('B');
  await expect(page.locator('#node-title')).toHaveText('type B');
  await expect(page.locator('#input-summary')).toContainText('"B"');
});

test('empty detail clears inspector and ignores obsolete full', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  await page.evaluate(() => { openExecution('B').catch(handleDashboardError); });
  await resolveRequest(page, 1, 200, nodes('B'));
  await page.click('#load-full-input');
  expect((await paths(page))[2]).toContain('/executions/B/nodes/node-B/full?side=input');
  await page.evaluate(() => { openExecution('EMPTY').catch(handleDashboardError); });
  await resolveRequest(page, 3, 200, {nodes: []});
  await resolveRequest(page, 2, 200, {value: {execution: 'B', secret: 'obsolete'}});
  await expect(page.locator('#canvas-title')).toHaveText('EMPTY');
  await expect(page.locator('#node-title')).toHaveText('Nenhum nó selecionado');
  await expect(page.locator('#input-summary')).toHaveText('{}');
  await expect(page.locator('#input-full')).toBeHidden();
  await expect(page.locator('#input-full')).not.toContainText('obsolete');
});

test('detail loading and 503 clear every prior detail field', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  await page.evaluate(() => { openExecution('A').catch(handleDashboardError); });
  await resolveRequest(page, 1, 200, nodes('A'));
  await page.evaluate(() => { openExecution('FAIL').catch(handleDashboardError); });
  await expect(page.locator('#canvas-title')).toHaveText('FAIL');
  await expect(page.locator('#node-title')).toHaveText('Nenhum nó selecionado');
  await expect(page.locator('#input-summary')).toHaveText('{}');
  await resolveRequest(page, 2, 503, {status: 'source_unavailable'});
  await expect(page.locator('#canvas-title')).toHaveText('FAIL');
  await expect(page.locator('#node-title')).toHaveText('Nenhum nó selecionado');
  await expect(page.locator('#metadata')).toHaveText('{}');
  await expect(page.locator('#node-error')).toHaveText('null');
  await expect(page.locator('#dashboard-alert')).toContainText('Fonte operacional indisponível');
});

test('SSE detail refresh clears loading state and remains clear after 503', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  await page.evaluate(() => { openExecution('A').catch(handleDashboardError); });
  await resolveRequest(page, 1, 200, nodes('A'));
  await page.evaluate(() => window.__eventSource.emit('change'));
  await resolveRequest(page, 2, 200, snapshot('refreshed', 2));
  await expect.poll(async () => (await paths(page)).length).toBe(4);
  await expect(page.locator('#node-title')).toHaveText('Nenhum nó selecionado');
  await expect(page.locator('#input-summary')).toHaveText('{}');
  await expect(page.locator('#nodes')).toBeEmpty();
  await resolveRequest(page, 3, 503, {status: 'source_unavailable'});
  await expect(page.locator('#node-title')).toHaveText('Nenhum nó selecionado');
  await expect(page.locator('#metadata')).toHaveText('{}');
  await expect(page.locator('#nodes')).toBeEmpty();
});

test('input and output full loads remain independent in both response orders', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  await page.evaluate(() => { openExecution('A').catch(handleDashboardError); });
  await resolveRequest(page, 1, 200, nodes('A'));
  await page.click('#load-full-input');
  await page.click('#load-full-output');
  await resolveRequest(page, 3, 200, {value: {side: 'output-first'}});
  await resolveRequest(page, 2, 200, {value: {side: 'input-second'}});
  await expect(page.locator('#input-full')).toContainText('input-second');
  await expect(page.locator('#output-full')).toContainText('output-first');
  await expect(page.locator('#input-full')).toBeVisible();
  await expect(page.locator('#output-full')).toBeVisible();

  await page.click('#load-full-input');
  await page.click('#load-full-output');
  await resolveRequest(page, 4, 200, {value: {side: 'input-first'}});
  await resolveRequest(page, 5, 200, {value: {side: 'output-second'}});
  await expect(page.locator('#input-full')).toContainText('input-first');
  await expect(page.locator('#output-full')).toContainText('output-second');
});

test('superseded detail and full errors cannot alter current detail or alert', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  await page.evaluate(() => { openExecution('A').catch(handleDashboardError); });
  await page.evaluate(() => { openExecution('B').catch(handleDashboardError); });
  await resolveRequest(page, 2, 200, nodes('B'));
  await resolveRequest(page, 1, 503, {status: 'source_unavailable'});
  await expect(page.locator('#node-title')).toHaveText('type B');
  await expect(page.locator('#dashboard-alert')).toBeHidden();

  await page.click('#load-full-input');
  await page.evaluate(() => { openExecution('C').catch(handleDashboardError); });
  await resolveRequest(page, 4, 200, nodes('C'));
  await resolveRequest(page, 3, 503, {status: 'source_unavailable'});
  await expect(page.locator('#node-title')).toHaveText('type C');
  await expect(page.locator('#input-full')).toBeHidden();
  await expect(page.locator('#dashboard-alert')).toBeHidden();
});

test('SSE ready does not clear a dashboard 503', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 503, {status: 'source_unavailable'});
  await page.evaluate(() => window.__eventSource.emit('ready'));
  await expect(page.locator('#dashboard-alert')).toContainText('Fonte operacional indisponível');
});

test('dashboard success does not clear degraded live state', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  await page.evaluate(() => window.__eventSource.emit('degraded'));
  await page.evaluate(() => { loadDashboard().catch(handleDashboardError); });
  await resolveRequest(page, 1, 200, snapshot('recovered-dashboard', 2));
  await expect(page.locator('#dashboard-alert')).toContainText('Atualização ao vivo indisponível');
  await expect(page.locator('#live-state')).toHaveText('Desconectado');
  await page.evaluate(() => window.__eventSource.emit('ready'));
  await expect(page.locator('#dashboard-alert')).toBeHidden();
  await expect(page.locator('#live-state')).toHaveText('Ao vivo');
});

test('reconnect clears only the live error after dashboard success', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  await page.evaluate(() => window.__eventSource.fail());
  await expect(page.locator('#dashboard-alert')).toContainText('Atualização ao vivo indisponível');
  await page.evaluate(() => window.__eventSource.emit('ready'));
  await expect(page.locator('#dashboard-alert')).toBeHidden();
});
"""


def test_javascript_runs_adversarial_interleavings_in_real_chromium() -> None:
    docker_probe = subprocess.run(
        ["docker", "image", "inspect", "mcr.microsoft.com/playwright:v1.55.0-noble"],
        capture_output=True,
        text=True,
        check=False,
    )
    if docker_probe.returncode != 0:
        pytest.fail(
            "Playwright Chromium container is unavailable: "
            + docker_probe.stdout
            + docker_probe.stderr
        )
    browser_cache = ROOT.parents[1] / "artifacts" / "ops-dashboard" / "playwright"
    package = browser_cache / "node_modules" / "@playwright" / "test" / "package.json"
    assert package.is_file(), f"cached @playwright/test package is unavailable: {browser_cache}"
    with tempfile.TemporaryDirectory(prefix="ops-browser-") as directory:
        os.chmod(directory, 0o755)
        spec = Path(directory) / "ops.spec.js"
        spec.write_text(BROWSER_SPEC, encoding="utf-8")
        spec.chmod(0o644)
        container = f"ops-browser-{os.getpid()}"
        started = subprocess.run(
            [
                "docker", "run", "--rm", "-d", "--name", container,
                "-v", f"{browser_cache / 'node_modules'}:/work/node_modules:ro",
                "mcr.microsoft.com/playwright:v1.55.0-noble", "sleep", "300",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert started.returncode == 0, started.stdout + started.stderr
        prepared = subprocess.run(
            ["docker", "exec", container, "mkdir", "-p", "/work", "/static"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert prepared.returncode == 0, prepared.stdout + prepared.stderr
        try:
            for source, target in (
                (spec, "/work/ops.spec.js"),
                (ROOT / "index.html", "/static/index.html"),
                (ROOT / "ops.js", "/static/ops.js"),
            ):
                copied = subprocess.run(
                    ["docker", "cp", str(source), f"{container}:{target}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert copied.returncode == 0, copied.stdout + copied.stderr
            completed = subprocess.run(
                [
                    "docker", "exec", "-w", "/work", container,
                    "./node_modules/.bin/playwright", "test", "ops.spec.js",
                    "--reporter=line", "--workers=1",
                ],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        finally:
            subprocess.run(
                ["docker", "rm", "-f", container],
                capture_output=True,
                text=True,
                check=False,
            )
    output = completed.stdout + completed.stderr
    print(output)
    assert completed.returncode == 0, output
    assert "Running 20 tests using 1 worker" in output, output
    assert "20 passed" in output, output
