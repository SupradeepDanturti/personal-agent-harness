import tempfile
import unittest
from pathlib import Path

from harness.agents.templates import WorkflowTemplates, validate_template
from harness.persistence.store import LocalStore


def sample(name="Evidence review"):
    return {"name": name, "specialists": [
        {"role": "Researcher", "prompt": "Find primary evidence.\nCite sources."},
        {"role": "Reviewer", "prompt": "Challenge assumptions."},
    ]}


class WorkflowTemplateTests(unittest.TestCase):
    def setUp(self):
        self.store = LocalStore(":memory:")
        self.templates = WorkflowTemplates(self.store)

    def tearDown(self):
        self.store.close()

    def test_save_select_delete_are_data_only(self):
        saved = self.templates.save(sample())
        self.assertEqual(self.templates.list(), [saved])
        selected = self.templates.select(saved["id"])
        selected["specialists"][0]["prompt"] = "Changed in editor"
        self.assertEqual(self.templates.select(saved["id"])["specialists"], sample()["specialists"])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM workflows").fetchone()[0], 0)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
        self.assertTrue(self.templates.delete(saved["id"]))
        self.assertEqual(self.templates.list(), [])
        with self.assertRaisesRegex(ValueError, "not found"):
            self.templates.select(saved["id"])

    def test_duplicate_name_never_overwrites(self):
        saved = self.templates.save(sample())
        other = sample(" evidence REVIEW ")
        other["specialists"][0]["prompt"] = "Replacement"
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.templates.save(other)
        self.assertEqual(self.templates.list(), [saved])

    def test_limit_is_durable(self):
        for index in range(20):
            self.templates.save(sample(str(index)))
        with self.assertRaisesRegex(ValueError, "20"):
            self.templates.save(sample("overflow"))
        self.templates.delete(self.templates.list()[0]["id"])
        self.templates.save(sample("replacement"))
        self.assertEqual(len(self.templates.list()), 20)

    def test_invalid_templates(self):
        invalid = [None, [], {}, {**sample(), "model": "other"},
                   {**sample(), "name": " "}, {**sample(), "name": "x" * 81},
                   {**sample(), "specialists": None}, {**sample(), "specialists": [None, {}]}]
        for count in (0, 1, 5):
            invalid.append({**sample(), "specialists": [sample()["specialists"][0]] * count})
        for field, value in (("role", ""), ("role", "x" * 101), ("prompt", " "),
                             ("prompt", "x" * 4001), ("prompt", 123)):
            data = sample()
            data["specialists"][0][field] = value
            invalid.append(data)
        # Templates cannot carry custom dependency edges or cyclic graphs.
        data = sample()
        data["specialists"][0]["dependsOn"] = ["specialist-1"]
        invalid.append(data)
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.templates.save(data)
        self.assertEqual(self.templates.list(), [])

    def test_boundaries_and_normalization(self):
        data = sample("x" * 80)
        data["specialists"] *= 2
        data["specialists"][0] = {"role": "x" * 100, "prompt": "x" * 4000}
        self.assertEqual(validate_template(data), data)
        self.assertEqual(validate_template(sample(" Name "))["name"], "Name")

    def test_persists_across_store_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "state.sqlite")
            store = LocalStore(path)
            saved = WorkflowTemplates(store).save(sample())
            store.close()
            store = LocalStore(path)
            try:
                self.assertEqual(WorkflowTemplates(store).list(), [saved])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
