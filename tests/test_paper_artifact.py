import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CASE_DIR = (
    ROOT
    / "research"
    / "collaborative-evolution"
    / "examples"
    / "engineering-everything"
)


class PaperArtifactCaseTest(unittest.TestCase):
    def test_feasibility_ledger_is_public_and_complete(self) -> None:
        ledger = CASE_DIR / "feasibility-ledger.jsonl"
        records = [
            json.loads(line)
            for line in ledger.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(
            {record["event_id"] for record in records},
            {"T01", "B01", "M01", "F01", "F02", "R01", "P01", "I01", "I02", "L01", "RB01"},
        )
        text = ledger.read_text(encoding="utf-8")
        self.assertNotIn("/Users/", text)
        self.assertNotIn("/home/", text)
        self.assertIn("${TEMP_HOME}", text)
        self.assertIn("${WORKSPACE}", text)

    def test_case_pins_public_revisions_and_claim_boundary(self) -> None:
        manifest = (CASE_DIR / "case-manifest.yaml").read_text(encoding="utf-8")
        readme = (CASE_DIR / "README.md").read_text(encoding="utf-8")
        for expected in (
            "abcd3bb26bb2c05236ac041d6cebf3af86a81357",
            "ba7468a61f701cf8b8643503b8e7082885af5d22",
            "6997b61d100708603bf80711a3d7c1604dc097fe",
            "evidence_artifact_release: v0.11.3",
        ):
            self.assertIn(expected, manifest)
        self.assertIn("does not establish cross-user effectiveness", readme)
        self.assertTrue((CASE_DIR / "reproduce.sh").exists())


if __name__ == "__main__":
    unittest.main()
