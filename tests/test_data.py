"""数据族转换测试：json/yaml/toml/xml/ini/plist/jsonl/csv/tsv。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import uniconvert  # noqa: F401  注册内置转换器
from uniconvert import convert
from uniconvert.core.job import Status


class TestDataFamily(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-data-"))

    def _run(self, src_name: str, src_text: str, dst_name: str, **opts):
        src = self.tmp / src_name
        src.write_text(src_text, encoding="utf-8")
        dst = self.tmp / dst_name
        result = convert(src, dst, **opts)
        self.assertEqual(result.status, Status.DONE, result.error)
        return dst.read_text(encoding="utf-8")

    def test_json_to_yaml(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML 未安装")
        out = self._run("a.json", '{"name": "测试", "n": 3}', "a.yaml")
        self.assertIn("name:", out)
        self.assertIn("测试", out)

    def test_yaml_to_toml(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML 未安装")
        out = self._run("a.yaml", "name: 测试\nnested:\n  a: 1\n  b: true\n", "a.toml")
        import tomllib

        data = tomllib.loads(out)
        self.assertEqual(data["name"], "测试")
        self.assertEqual(data["nested"]["a"], 1)

    def test_json_to_toml_roundtrip(self):
        payload = {"title": "x", "tags": ["a", "b"], "meta": {"k": 1.5}, "ok": True}
        out = self._run("b.json", json.dumps(payload, ensure_ascii=False), "b.toml")
        import tomllib

        self.assertEqual(tomllib.loads(out), payload)

    def test_json_to_xml(self):
        out = self._run("c.json", '{"root": {"@id": "1", "name": "n", "item": [1, 2]}}', "c.xml")
        self.assertIn("<root", out)
        self.assertIn('id="1"', out)
        self.assertIn("<item>1</item>", out)

    def test_xml_to_json(self):
        out = self._run(
            "d.xml",
            '<?xml version="1.0"?><doc><a x="1">t</a><b/><b/></doc>',
            "d.json",
        )
        data = json.loads(out)
        self.assertEqual(data["doc"]["a"]["@x"], "1")
        self.assertEqual(data["doc"]["a"]["#text"], "t")
        self.assertEqual(data["doc"]["b"], ["", ""])

    def test_json_to_ini(self):
        out = self._run(
            "e.json",
            '{"server": {"host": "localhost", "port": 8080}}',
            "e.ini",
        )
        self.assertIn("[server]", out)
        self.assertIn("port = 8080", out)

    def test_ini_to_json(self):
        out = self._run("f.ini", "[a]\nx = 1\ny = two\n", "f.json")
        self.assertEqual(json.loads(out), {"a": {"x": "1", "y": "two"}})

    def test_plist_to_json(self):
        import plistlib

        src = self.tmp / "g.plist"
        src.write_bytes(plistlib.dumps({"a": 1, "b": "中文"}, fmt=plistlib.FMT_XML))
        dst = self.tmp / "g.json"
        r = convert(src, dst)
        self.assertEqual(r.status, Status.DONE, r.error)
        self.assertEqual(json.loads(dst.read_text(encoding="utf-8")), {"a": 1, "b": "中文"})

    def test_jsonl_to_json(self):
        out = self._run("h.jsonl", '{"a":1}\n{"a":2}\n', "h.json")
        self.assertEqual(json.loads(out), [{"a": 1}, {"a": 2}])

    def test_csv_to_json(self):
        out = self._run("i.csv", "name,age\n张三,30\n李四,25\n", "i.json")
        data = json.loads(out)
        self.assertEqual(data[0]["name"], "张三")
        self.assertEqual(data[1]["age"], "25")

    def test_csv_to_tsv(self):
        out = self._run("j.csv", "a,b\n1,2\n", "j.tsv")
        self.assertIn("a\tb", out)

    def test_json_self_reformat(self):
        out = self._run("k.json", '{"b":1,"a":[1,2]}', "k2.json", sort_keys=True)
        self.assertEqual(json.loads(out), {"b": 1, "a": [1, 2]})
        self.assertLess(out.index('"a"'), out.index('"b"'))

    def test_json_minify(self):
        out = self._run("l.json", '{\n  "a": 1,\n  "b": 2\n}', "l2.json", indent=-1)
        self.assertEqual(out.strip(), '{"a": 1, "b": 2}')

    def test_csv_delimiter_change(self):
        out = self._run("m.csv", "a,b\n1,2\n", "m2.csv", delimiter=";")
        self.assertIn("a;b", out)

    def test_toml_top_level_must_be_table(self):
        out = self._run("n.json", "[1, 2, 3]", "n.toml") if False else None
        # 数组顶层写 TOML 应当失败而不是产出坏文件
        src = self.tmp / "n.json"
        src.write_text("[1,2,3]", encoding="utf-8")
        dst = self.tmp / "n.toml"
        r = convert(src, dst)
        self.assertEqual(r.status, Status.FAILED)
        self.assertIn("TOML", r.error)

    def test_failed_parse_gives_clean_error(self):
        src = self.tmp / "bad.json"
        src.write_text("{not json", encoding="utf-8")
        r = convert(src, self.tmp / "bad.yaml")
        self.assertEqual(r.status, Status.FAILED)
        self.assertIn("JSON 解析失败", r.error)


if __name__ == "__main__":
    unittest.main()
