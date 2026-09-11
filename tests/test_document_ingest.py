import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "ingestion-worker"))
sys.modules.setdefault("boto3", types.ModuleType("boto3"))
sys.modules.setdefault("psycopg", types.ModuleType("psycopg"))

from app.document_ingest import PASSAGE_CHAR_LIMIT, build_passages, parse_rst


class DocumentIngestTests(unittest.TestCase):
    def test_heading_and_line_locator(self):
        blocks = parse_rst(b"Title\n=====\n\nBody line one.\nBody line two.\n")
        self.assertEqual(blocks[0], {
            "kind": "heading", "text": "Title", "line_start": 1, "line_end": 2,
            "structure": {"adornment": "="},
        })
        self.assertEqual((blocks[1]["line_start"], blocks[1]["line_end"]), (4, 5))

    def test_nonblank_content_is_preserved(self):
        source = ":nosearch:\n\nParagraph one.\ncontinued.\n\n.. note::\n   Important.\n"
        blocks = parse_rst(source.encode())
        reconstructed = "\n".join(block["text"] for block in blocks)
        for line in source.splitlines():
            if line.strip():
                self.assertIn(line, reconstructed)

    def test_large_content_splits_without_losing_blocks(self):
        blocks = [
            {"kind": "paragraph", "text": "x" * (PASSAGE_CHAR_LIMIT // 2),
             "line_start": i + 1, "line_end": i + 1, "structure": {}}
            for i in range(3)
        ]
        passages = build_passages(blocks)
        self.assertGreater(len(passages), 1)
        self.assertEqual(sum(len(p["spans"]) for p in passages), 3)

    def test_invalid_utf8_is_rejected(self):
        with self.assertRaises(UnicodeDecodeError):
            parse_rst(b"\xff\xfe\xfd")


if __name__ == "__main__":
    unittest.main()
