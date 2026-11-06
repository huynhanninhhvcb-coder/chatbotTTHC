from __future__ import annotations

import unittest

from services.interview_service import TEMPLATE_STEPS
from services.template_service import TEMPLATE_GROUPS, TEMPLATES, get_template, list_templates

IDENTITY = ("full_name", "date_of_birth", "citizen_id", "residence_address")


class TemplateServiceTests(unittest.TestCase):
    def test_lists_every_form_in_templates_word(self):
        ids = {item["id"] for item in list_templates()}
        self.assertEqual(
            ids,
            {
                "tro_cap_huu_tri",
                "ho_tro_mai_tang",
                "ho_tro_hoa_tang",
                "ho_tro_nq40",
                "ho_tro_nq32",
                "xac_dinh_khuyet_tat",
                "to_khai_bhyt",
                "xn_huu_tri_tu_khai",
                "xn_huu_tri_khai_thay",
                "xn_mung_tho_tu_khai",
                "xn_mung_tho_khai_thay",
                "xn_khuyet_tat_tu_khai",
                "xn_khuyet_tat_khai_thay",
                "xn_nq35",
                "xn_nq40",
                "xn_nq32",
            },
        )

    def test_every_template_belongs_to_a_group_and_has_an_interview(self):
        for template in TEMPLATES.values():
            with self.subTest(template=template.id):
                self.assertIn(template.group, TEMPLATE_GROUPS)
                self.assertIn(template.id, TEMPLATE_STEPS)

    def test_every_required_field_is_asked_during_the_interview(self):
        for template in TEMPLATES.values():
            asked = {step.field for step in TEMPLATE_STEPS[template.id]}
            with self.subTest(template=template.id):
                self.assertTrue(set(template.required_fields) <= asked, set(template.required_fields) - asked)

    def test_self_and_proxy_versions_share_a_family(self):
        for family in ("xn_huu_tri", "xn_mung_tho", "xn_khuyet_tat"):
            variants = {t.variant for t in TEMPLATES.values() if t.family_id == family}
            with self.subTest(family=family):
                self.assertEqual(variants, {"tu_khai", "khai_thay"})

    def test_deceased_benefit_templates_require_deceased_and_their_category(self):
        hoa_tang = get_template("ho_tro_hoa_tang")
        mai_tang = get_template("ho_tro_mai_tang")
        self.assertEqual(hoa_tang.required_fields[:4], IDENTITY)
        self.assertIn("cremation_support_category", hoa_tang.required_fields)
        self.assertIn("burial_support_category", mai_tang.required_fields)
        self.assertIn("payment_method", mai_tang.required_fields)

    def test_nq_templates_require_identity_issue_and_support_group(self):
        expected = (*IDENTITY, "citizen_id_issue_date", "citizen_id_issue_place", "support_category")
        self.assertEqual(get_template("ho_tro_nq40").required_fields, expected)
        self.assertEqual(get_template("ho_tro_nq32").required_fields, expected)

    def test_unknown_template_returns_none(self):
        self.assertIsNone(get_template("khong-ton-tai"))


if __name__ == "__main__":
    unittest.main()
