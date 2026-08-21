from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MockupContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "index.html").read_text(encoding="utf-8")
        cls.css = (ROOT / "styles.css").read_text(encoding="utf-8")
        cls.js = (ROOT / "app.js").read_text(encoding="utf-8")

    def test_semantic_dashboard_regions_exist(self):
        for token in (
            'id="app-sidebar"',
            'id="dashboard-header"',
            'id="kpi-grid"',
            'id="commercial-funnel"',
            'id="service-volume-chart"',
            'id="interest-mix-chart"',
            'id="handoff-reasons-chart"',
            'id="services-table-body"',
            'id="service-drawer"',
        ):
            self.assertIn(token, self.html)

    def test_demo_label_and_read_only_copy_are_visible(self):
        self.assertIn("Dados demonstrativos", self.html)
        self.assertIn("Painel visual sem conexão com dados reais", self.html)
        self.assertIn('class="mobile-demo-pill"', self.html)

    def test_selected_range_updates_analytics_caption(self):
        self.assertIn('id="analytics-range-label"', self.html)
        self.assertIn('$("analytics-range-label").textContent', self.js)

    def test_approved_palette_is_declared(self):
        for color in (
            "#245634",
            "#173A27",
            "#005A2A",
            "#F4E8D7",
            "#FFFCF7",
            "#E4D5C0",
            "#66766B",
            "#E85F67",
            "#2F7D4A",
            "#327E8F",
            "#C99135",
            "#B7443E",
            "#A99C8A",
        ):
            self.assertIn(color.lower(), self.css.lower())

    def test_accessible_controls_exist(self):
        self.assertRegex(
            self.html,
            r'<button[^>]+aria-label="Fechar detalhes do atendimento"',
        )
        self.assertIn('aria-label="Buscar atendimento demonstrativo"', self.html)
        self.assertIn('aria-live="polite"', self.html)

    def test_responsive_breakpoints_exist(self):
        self.assertGreaterEqual(len(re.findall(r"@media", self.css)), 2)

    def test_synthetic_dataset_and_interaction_functions_exist(self):
        self.assertIn("const SERVICES = Object.freeze([", self.js)
        self.assertGreaterEqual(self.js.count('id: "demo-'), 12)
        for function_name in (
            "getRangeModel",
            "filterServices",
            "renderDashboard",
            "renderKpis",
            "renderCharts",
            "renderServices",
            "openServiceDrawer",
            "closeServiceDrawer",
            "setActiveNavigation",
            "simulateRefresh",
        ):
            self.assertRegex(self.js, rf"function\s+{function_name}\s*\(")

    def test_dataset_covers_required_operational_states(self):
        for journey_state in (
            '"completed"',
            '"active"',
            '"handoff"',
            '"failed"',
        ):
            self.assertIn(journey_state, self.js)
        for business_state in (
            'paymentStatus: "Pendente"',
            'reservationStatus: "Confirmada"',
            'handoffStatus: "Solicitado"',
            'mayaStatus: "Falha"',
        ):
            self.assertIn(business_state, self.js)

    def test_mockup_has_no_business_network_or_effect_calls(self):
        lowered = self.js.lower()
        for forbidden in (
            "fetch(",
            "xmlhttprequest",
            "websocket",
            "eventsource",
            "payment_link",
            "request_handoff",
            "create_reservation",
            "send_message",
            "retry_execution",
        ):
            self.assertNotIn(forbidden, lowered)

    def test_drawer_supports_escape_and_background_close(self):
        self.assertIn('event.key === "Escape"', self.js)
        self.assertIn('drawer-backdrop', self.js)

    def test_readme_documents_preview_and_scope(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("python -m http.server 8088", readme)
        self.assertIn("Dados demonstrativos", readme)
        self.assertIn("não acessa banco", readme.lower())
        self.assertIn("não executa reservas", readme.lower())

    def test_no_remote_assets_or_production_endpoints(self):
        combined = (self.html + self.css + self.js).lower()
        for forbidden in (
            "https://",
            "http://",
            "leads-hermes.chapadabackpackers.com",
            "hermes.chapadabackpackers.com/ops",
            "/ops/api/",
        ):
            self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()
