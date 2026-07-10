from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "china_edu_book.py"
SPEC = importlib.util.spec_from_file_location("china_edu_book", MODULE_PATH)
assert SPEC and SPEC.loader
book = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = book
SPEC.loader.exec_module(book)

RID = "4f64356a-8df7-4579-9400-e32c9a7f6718"
RID2 = "5cd7e623-5c38-4602-871a-3fba8a551db2"


def raw_record(
    resource_id: str = RID,
    *,
    title: str = "义务教育教科书 数学 三年级 上册",
    stage: str = "小学",
    subject: str = "数学",
    version: str = "人教版",
    grade: str = "三年级",
    volume: str = "上册",
) -> dict:
    names = [
        ("zxxxd", stage),
        ("zxxxk", subject),
        ("zxxbb", version),
        ("zxxnj", grade),
        ("zxxcc", volume),
    ]
    return {
        "id": resource_id,
        "title": title,
        "description": "测试教材",
        "custom_properties": {"format": "pdf", "size": 12345},
        "provider_list": [{"name": "人民教育出版社"}],
        "tag_list": [
            {
                "tag_dimension_id": dimension,
                "tag_name": name,
                "tag_id": f"tag-{index}",
            }
            for index, (dimension, name) in enumerate(names)
        ],
    }


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, url: str) -> None:
        super().__init__(body)
        self._url = url
        self.headers = {"Content-Type": "application/pdf"}

    def geturl(self) -> str:
        return self._url


class FakeClient(book.HttpClient):
    def __init__(self, body: bytes) -> None:
        super().__init__(timeout=1, retries=0)
        self.body = body

    def open(self, url: str, **kwargs):  # type: ignore[override]
        return FakeResponse(self.body, url)


class IdentifierTests(unittest.TestCase):
    def test_extract_plain_resource_id(self) -> None:
        self.assertEqual(book.extract_resource_id(RID.upper()), RID)

    def test_extract_resource_id_from_detail_url(self) -> None:
        url = (
            "https://basic.smartedu.cn/tchMaterial/detail?"
            f"contentType=assets_document&contentId={RID}&catalogType=tchMaterial"
        )
        self.assertEqual(book.extract_resource_id(url), RID)

    def test_extract_resource_id_rejects_other_text(self) -> None:
        with self.assertRaises(book.SkillError):
            book.extract_resource_id("not-a-resource")

    def test_split_resource_ids_deduplicates(self) -> None:
        values = [f"{RID},{RID2}", RID]
        self.assertEqual(book.split_resource_ids(values), [RID, RID2])


class UrlSafetyTests(unittest.TestCase):
    def test_official_https_url_is_allowed(self) -> None:
        url = "https://r1-ndr.ykt.cbern.com.cn/path/book.pdf"
        self.assertEqual(book.ensure_official_url(url), url)

    def test_non_https_url_is_rejected(self) -> None:
        with self.assertRaises(book.SkillError):
            book.ensure_official_url("http://basic.smartedu.cn/book.pdf")

    def test_unrelated_host_is_rejected(self) -> None:
        with self.assertRaises(book.SkillError):
            book.ensure_official_url("https://example.com/book.pdf")

    def test_quote_url_path_preserves_query(self) -> None:
        url = "https://r1-ndr.ykt.cbern.com.cn/a/语文 八年级.pdf?x=1"
        quoted = book.quote_url_path(url)
        self.assertIn("%E8%AF%AD%E6%96%87%20%E5%85%AB%E5%B9%B4%E7%BA%A7.pdf", quoted)
        self.assertTrue(quoted.endswith("?x=1"))


class AuthenticationTests(unittest.TestCase):
    def test_find_access_token_in_nested_json_string(self) -> None:
        nested = {
            "value": json.dumps(
                {"value": json.dumps({"access_token": "secret-access-token"})}
            )
        }
        self.assertEqual(book.find_access_token(nested), "secret-access-token")

    def test_storage_state_extracts_token_and_cookie(self) -> None:
        state = {
            "cookies": [
                {"name": "session", "value": "abc", "domain": ".smartedu.cn"},
                {"name": "ignore", "value": "x", "domain": ".example.com"},
            ],
            "origins": [
                {
                    "origin": "https://basic.smartedu.cn",
                    "localStorage": [
                        {
                            "name": "ND_UC_AUTH_1",
                            "value": json.dumps(
                                {"value": json.dumps({"access_token": "token-123"})}
                            ),
                        }
                    ],
                }
            ],
        }
        self.assertEqual(book.token_from_storage_state(state), "token-123")
        self.assertEqual(book.cookies_from_storage_state(state), "session=abc")

    def test_load_auth_reads_storage_state(self) -> None:
        state = {
            "cookies": [{"name": "sid", "value": "1", "domain": "basic.smartedu.cn"}],
            "origins": [
                {
                    "localStorage": [
                        {
                            "name": "ND_UC_AUTH",
                            "value": json.dumps({"access_token": "from-state"}),
                        }
                    ]
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            path.write_text(json.dumps(state), encoding="utf-8")
            args = argparse.Namespace(
                access_token=None,
                access_token_file=None,
                cookie=None,
                storage_state=str(path),
            )
            auth = book.load_auth(args)
        self.assertEqual(auth.access_token, "from-state")
        self.assertEqual(auth.cookie, "sid=1")


class CatalogTests(unittest.TestCase):
    def test_parse_version_urls_supports_string_and_list(self) -> None:
        one = "https://s-file-1.ykt.cbern.com.cn/a.json"
        two = "https://s-file-2.ykt.cbern.com.cn/b.json"
        self.assertEqual(book.parse_version_urls({"urls": f"{one},{two}"}), [one, two])
        self.assertEqual(book.parse_version_urls({"urls": [one, two]}), [one, two])

    def test_normalize_record_maps_tag_dimensions(self) -> None:
        normalized = book.normalize_record(raw_record())
        assert normalized is not None
        self.assertEqual(normalized["id"], RID)
        self.assertEqual(normalized["stage"], "小学")
        self.assertEqual(normalized["grade"], "三年级")
        self.assertEqual(normalized["subject"], "数学")
        self.assertEqual(normalized["version"], "人教版")
        self.assertEqual(normalized["volume"], "上册")
        self.assertEqual(normalized["providers"], ["人民教育出版社"])

    def test_normalize_record_ignores_non_uuid(self) -> None:
        record = raw_record(resource_id="bad-id")
        self.assertIsNone(book.normalize_record(record))

    def test_search_catalog_uses_structured_filters(self) -> None:
        first = book.normalize_record(raw_record())
        second = book.normalize_record(
            raw_record(
                resource_id=RID2,
                title="义务教育教科书 语文 三年级 下册",
                subject="语文",
                volume="下册",
            )
        )
        assert first and second
        matches = book.search_catalog(
            [first, second],
            stage="小学",
            grade="三年级",
            subject="数学",
            version="人教版",
            volume="上册",
        )
        self.assertEqual([item["id"] for item in matches], [RID])

    def test_search_catalog_matches_publisher_alias(self) -> None:
        record = raw_record(version="人民教育出版社")
        normalized = book.normalize_record(record)
        assert normalized
        matches = book.search_catalog([normalized], version="人教版")
        self.assertEqual(len(matches), 1)

    def test_search_catalog_query_requires_each_term(self) -> None:
        normalized = book.normalize_record(raw_record())
        assert normalized
        self.assertEqual(len(book.search_catalog([normalized], query="数学 上册")), 1)
        self.assertEqual(len(book.search_catalog([normalized], query="数学 下册")), 0)


class DownloadCandidateTests(unittest.TestCase):
    def test_expand_storage_builds_public_variants_without_token(self) -> None:
        storage = "cs_path:${ref-path}/edu_product/esp/assets/book.pkg/教材.pdf"
        urls = list(book.expand_storage(storage, prefer_private=False))
        self.assertTrue(urls[0].startswith("https://r1-ndr.ykt.cbern.com.cn/"))
        self.assertTrue(any("r1-ndr-private" in url for url in urls))

    def test_pdf_candidates_prefer_source_and_include_legacy(self) -> None:
        source = (
            "https://r1-ndr-private.ykt.cbern.com.cn/"
            "edu_product/esp/assets/book.pkg/教材.pdf"
        )
        detail = {
            "ti_items": [
                {
                    "ti_format": "jpg",
                    "ti_storage": "https://r1-ndr.ykt.cbern.com.cn/cover.jpg",
                },
                {
                    "ti_format": "pdf",
                    "ti_file_flag": "source",
                    "ti_is_source_file": True,
                    "ti_storage": source,
                },
            ]
        }
        urls = book.pdf_url_candidates(detail, RID, book.AuthContext())
        self.assertIn("r1-ndr.ykt.cbern.com.cn", urls[0])
        self.assertTrue(urls[-1].endswith(f"/{RID}.pkg/pdf.pdf"))
        self.assertFalse(any(url.endswith("cover.jpg") for url in urls))

    def test_pdf_candidates_prefer_private_with_token(self) -> None:
        detail = {
            "ti_items": [
                {
                    "ti_format": "pdf",
                    "ti_is_source_file": True,
                    "ti_storage": (
                        "https://r1-ndr-private.ykt.cbern.com.cn/"
                        "edu_product/esp/assets/book.pkg/教材.pdf"
                    ),
                }
            ]
        }
        urls = book.pdf_url_candidates(detail, RID, book.AuthContext("token"))
        self.assertIn("r1-ndr-private.ykt.cbern.com.cn", urls[0])


class FileTests(unittest.TestCase):
    def test_safe_filename_removes_forbidden_characters(self) -> None:
        value = book.safe_filename('CON: 三年级/数学?*.pdf')
        self.assertNotRegex(value, r'[\\/:*?"<>|]')
        self.assertTrue(value.endswith(".pdf"))

    def test_download_pdf_writes_and_hashes_valid_file(self) -> None:
        payload = b"%PDF-1.7\nbody\n%%EOF\n"
        client = FakeClient(payload)
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "book.pdf"
            result = client.download_pdf(
                "https://r1-ndr.ykt.cbern.com.cn/book.pdf", target
            )
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(result["size"], len(payload))
            self.assertEqual(result["sha256"], book.sha256_file(target))
            self.assertFalse((target.parent / f".{target.name}.part").exists())

    def test_download_pdf_rejects_html_and_removes_partial_file(self) -> None:
        client = FakeClient(b"<html>login</html>")
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "book.pdf"
            with self.assertRaises(book.SkillError):
                client.download_pdf(
                    "https://r1-ndr.ykt.cbern.com.cn/book.pdf", target
                )
            self.assertFalse(target.exists())
            self.assertFalse((target.parent / f".{target.name}.part").exists())

    def test_metadata_path_uses_book_dimensions(self) -> None:
        normalized = book.normalize_record(raw_record())
        assert normalized
        path = book.metadata_path(normalized, Path("library"))
        self.assertEqual(
            path,
            Path("library") / "小学" / "三年级" / "数学" / "人教版" / "上册",
        )


class CliTests(unittest.TestCase):
    def test_parser_accepts_search_version_filter(self) -> None:
        args = book.build_parser().parse_args(
            ["search", "--stage", "小学", "--subject", "数学", "--version", "人教版"]
        )
        self.assertEqual(args.version, "人教版")
        self.assertEqual(args.command, "search")

    def test_doctor_offline_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            code = book.main(
                ["doctor", "--cache-dir", temporary, "--json"]
            )
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
