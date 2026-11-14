"""Regression checks for bounded local retrieval and provider-reported usage."""
import unittest
from harness.persistence.store import LocalStore
from harness.knowledge.memory import build_context, CONTEXT_CHARACTER_LIMIT


class ContextBudgetTests(unittest.TestCase):
    def setUp(self): self.store = LocalStore(':memory:')
    def tearDown(self): self.store.close()

    def test_old_relevant_record_survives_large_recent_turns(self):
        old = self.store.add_message('assistant', 'Orion migration uses staged SQLite checkpoints.')
        for _ in range(30): self.store.add_message('user', 'Unrelated long turn ' + 'x' * 11000)
        context = build_context(self.store, 'Orion migration')
        self.assertIn(old['id'], context)
        self.assertIn('staged SQLite checkpoints', context)
        self.assertLessEqual(len(context), CONTEXT_CHARACTER_LIMIT)
        self.assertIn('[excerpt; use recall_read]', context)
        self.assertNotIn('x' * 1000, context)

    def test_context_retrieval_has_no_model_dependency_and_tracks_deletion(self):
        old = self.store.add_message('assistant', 'Zephyr deployment result is retained.')
        self.assertIn(old['id'], build_context(self.store, 'Zephyr'))
        self.store.db.execute('DELETE FROM messages WHERE id=?', (old['id'],))
        self.assertNotIn('Zephyr deployment result', build_context(self.store, 'Zephyr'))

    def test_budget_holds_with_large_summary_people_memory_and_goals(self):
        self.store.set_setting('conversationSummary', {'text': 'summary ' * 3000})
        for i in range(20):
            self.store.save_memory({'text': f'Preference {i} ' + 'm' * 1500, 'source': 'user', 'provenance': 'explicit'})
            self.store.add_message('user', 'question ' * 1500)
        self.assertLessEqual(len(build_context(self.store, 'Preference')), CONTEXT_CHARACTER_LIMIT)

    def test_valid_long_chat_does_not_exceed_recall_query_limit(self):
        context = build_context(self.store, 'long chat request ' * 650)
        self.assertLessEqual(len(context), CONTEXT_CHARACTER_LIMIT)
