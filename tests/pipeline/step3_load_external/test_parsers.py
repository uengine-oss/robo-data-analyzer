from __future__ import annotations

import unittest

from graph.schema.node_ids import project_file_id
from pipeline.steps.step3_load_external.parsers.config_parser import parse_config
from pipeline.steps.step3_load_external.parsers.markdown_doc import parse_markdown
from pipeline.steps.step3_load_external.parsers.xml_universal import parse_xml


class ExternalParsersTest(unittest.TestCase):
    def test_config_formats_preserve_full_values(self) -> None:
        long_value = "가" * 1400
        cases = (
            (f'{{"shop": {{"title": "{long_value}"}}}}', ".json", "shop.title", long_value),
            ("shop.title=쇼핑몰\ncontinued=one\\\n  two", ".properties", "shop.title", "쇼핑몰"),
            ("[shop]\ntitle = '쇼핑몰'", ".toml", "shop.title", "쇼핑몰"),
            ("[shop]\ntitle=쇼핑몰", ".ini", "shop.title", "쇼핑몰"),
            ("shop:\n  title: 쇼핑몰", ".yaml", "shop.title", "쇼핑몰"),
            ("id,name\nORDER,주문", ".csv", "ORDER", "id=ORDER; name=주문"),
        )
        for text, suffix, key, expected in cases:
            with self.subTest(suffix=suffix):
                by_key = {entry.key: entry.value for entry in parse_config(text, suffix)}
                self.assertEqual(expected, by_key[key])

    def test_xml_is_safe_and_ignores_comment_sql(self) -> None:
        text = """<?xml version='1.0' encoding='EUC-KR'?>
<!DOCTYPE mapper [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>
<mapper namespace='shop.Order'>
  <!-- SELECT * FROM SECRET -->
  <select id='find'>SELECT * FROM ORDERS WHERE name='UPDATE X SET Y=1'</select>
  <bean id='orderService' class='shop.service.OrderService'/>
</mapper>"""
        entries = parse_xml(text)
        sql = [entry for entry in entries if entry.kind == "sql"]
        self.assertEqual(["shop.Order.find"], [entry.key for entry in sql])
        self.assertNotIn("SECRET", sql[0].text)
        self.assertIn("orderService", [entry.key for entry in entries])

    def test_markdown_keeps_nested_hierarchy_and_bounded_chunks(self) -> None:
        file_id = project_file_id("shop", "README.md")
        nodes, edges = parse_markdown(file_id, "README.md", "# 주문\n소개\n\n## 조회\n" + "가" * 1500)
        labels = [node.label for node in nodes]
        self.assertEqual("DOCUMENT", labels[0])
        self.assertEqual(2, labels.count("DOC_SECTION"))
        self.assertGreaterEqual(labels.count("DOC_CHUNK"), 3)
        self.assertTrue(all(len(node.text) <= 1200 for node in nodes if node.label == "DOC_CHUNK"))
        section_ids = [node.id for node in nodes if node.label == "DOC_SECTION"]
        child_section = next(node for node in nodes if node.id == section_ids[1])
        self.assertEqual(section_ids[0], child_section.parent_id)
        # ProjectFile→DOCUMENT plus one parent edge for every remaining node.
        self.assertEqual(len(nodes), len(edges))

if __name__ == "__main__":
    unittest.main()
