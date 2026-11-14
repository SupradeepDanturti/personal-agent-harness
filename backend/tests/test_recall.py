import json
import unittest
from types import SimpleNamespace

from harness.api.routes import dispatch
from harness.application.runtime import Runtime
from harness.knowledge.recall import RecallIndex, initialize_recall
from harness.persistence.store import LocalStore
from tests.offline_loop import OfflineAsyncCase


class RecallTests(unittest.TestCase):
    def setUp(self):
        self.store = LocalStore(':memory:')
        self.recall = RecallIndex(self.store)

    def tearDown(self):
        self.store.close()

    def test_old_chats_and_specialist_results_survive_newest_500_boundary(self):
        message = self.store.add_message('user', 'Historic bicycle repair discussion')
        task = self.store.create_task({'prompt': 'Inspect derailleur', 'parentId': 'workflow-parent'})
        self.store.update_task(task['id'], {'status': 'running'})
        self.store.update_task(task['id'], {'status': 'completed', 'result': 'Derailleur verified by specialist'})
        for i in range(505):
            self.store.add_message('assistant', f'Later unrelated chat {i}')
            self.store.create_task({'prompt': f'Later unrelated work {i}'})
        self.assertEqual(self.recall.search('bicycle')['results'][0]['id'], message['id'])
        found = self.recall.search('verified')['results'][0]
        self.assertEqual((found['kind'], found['id']), ('task', task['id']))
        self.assertIn('specialist', self.recall.read('task', task['id'])['content'])

    def test_backfill_idempotence_and_source_edits_deletes(self):
        self.store.db.execute('DROP TRIGGER recall_message_added')
        message = self.store.add_message('user', 'legacybackfillmarker')
        initialize_recall(self.store.db)
        initialize_recall(self.store.db)
        self.assertEqual(len(self.recall.search('legacybackfillmarker')['results']), 1)
        self.store.db.execute('UPDATE messages SET content=? WHERE id=?', ('replacementmarker', message['id']))
        self.assertEqual(self.recall.search('legacybackfillmarker')['results'], [])
        self.assertEqual(self.recall.read('message', message['id'])['content'], 'replacementmarker')
        self.store.db.execute('DELETE FROM messages WHERE id=?', (message['id'],))
        self.assertEqual(self.recall.search('replacementmarker')['results'], [])
        with self.assertRaisesRegex(ValueError, 'not found'):
            self.recall.read('message', message['id'])

    def test_forget_redacts_tasks_and_removes_action_results_from_index(self):
        phrase = 'privateforgetmarker'
        message = self.store.add_message('user', phrase)
        memory = self.store.save_memory({'text': phrase, 'source': message['id'], 'provenance': 'explicit'})
        task = self.store.create_task({'prompt': phrase})
        self.store.append_event('action.completed', {'result': phrase})
        self.assertEqual({r['kind'] for r in self.recall.search(phrase)['results']}, {'message', 'memory', 'task', 'action'})
        self.store.forget_memory(memory['id'], include_source_messages=True)
        self.assertEqual(self.recall.search(phrase)['results'], [])
        self.assertIn('[forgotten]', self.recall.read('task', task['id'])['content'])

    def test_source_rowid_collisions_and_rolled_back_changes_preserve_index(self):
        message = self.store.add_message('user', 'samekeywordoriginal')
        task = self.store.create_task({'prompt': 'samekeywordoriginal'})
        self.assertEqual(len(self.recall.search('samekeywordoriginal')['results']), 2)
        self.store.db.execute('BEGIN')
        self.store.db.execute('UPDATE messages SET content=? WHERE id=?', ('changedkeyword', message['id']))
        self.store.db.execute('DELETE FROM tasks WHERE id=?', (task['id'],))
        self.store.db.execute('ROLLBACK')
        self.assertEqual(len(self.recall.search('samekeywordoriginal')['results']), 2)
        self.assertEqual(self.recall.search('changedkeyword')['results'], [])

    def test_workflow_plan_and_only_completed_actions_are_searchable(self):
        self.store.db.execute('INSERT INTO workflows VALUES (?,?,?)', ('w1', json.dumps({'prompt': 'researchmarker'}), 10))
        self.store.append_event('tool.requested', {'operation': 'secretrequestmarker'})
        self.store.append_event('action.completed', {'result': 'finishedresultmarker'})
        self.assertEqual(self.recall.search('researchmarker')['results'][0]['kind'], 'workflow')
        self.assertEqual(self.recall.search('secretrequestmarker')['results'], [])
        self.assertEqual(self.recall.search('finishedresultmarker')['results'][0]['kind'], 'action')
        # An event changing classification must disappear from the projection.
        self.store.db.execute("UPDATE events SET type='tool.requested' WHERE type='action.completed'")
        self.assertEqual(self.recall.search('finishedresultmarker')['results'], [])

    def test_query_operators_are_literal_and_output_is_bounded(self):
        message = self.store.add_message('user', 'longmarker ' + 'x' * 30000)
        self.assertEqual(self.recall.search('" OR * ( ) : NEAR') ['modelCalls'], 0)
        self.assertLessEqual(len(self.recall.search('longmarker')['results'][0]['excerpt']), 1200)
        result = self.recall.read('message', message['id'])
        self.assertEqual(len(result['content']), 12000)
        self.assertTrue(result['truncated'])
        for invalid in ('', 'a' * 1001, None):
            with self.assertRaises(ValueError): self.recall.search(invalid)
        with self.assertRaises(ValueError): self.recall.read('messages; DROP TABLE messages', 'id')


class RecallApiTests(OfflineAsyncCase):
    async def test_specialist_can_search_and_read_without_write_privileges(self):
        store = LocalStore(':memory:')
        try:
            message = store.add_message('assistant', 'readonlyrecallmarker')
            runtime = SimpleNamespace(store=store, recall=RecallIndex(store))
            result = await Runtime.operate(runtime, 'recall_search', {'query': 'readonlyrecallmarker'}, read_only=True)
            self.assertEqual(result['results'][0]['id'], message['id'])
            result = await Runtime.operate(runtime, 'recall_read', {'kind': 'message', 'id': message['id']}, read_only=True)
            self.assertEqual(result['content'], 'readonlyrecallmarker')
            with self.assertRaisesRegex(ValueError, 'read-only'):
                await Runtime.operate(runtime, 'notify', {'title': 'not permitted'}, read_only=True)
        finally:
            store.close()

    async def test_routes_retrieve_current_sources_without_model(self):
        store = LocalStore(':memory:')
        try:
            message = store.add_message('user', 'apirecallmarker')
            runtime = SimpleNamespace(recall=RecallIndex(store))
            result = await dispatch(runtime, 'POST', '/api/recall/search', {'query': 'apirecallmarker'})
            self.assertEqual(json.loads(result.body)['results'][0]['id'], message['id'])
            result = await dispatch(runtime, 'GET', '/api/recall/item', query={'kind': 'message', 'id': message['id']})
            self.assertEqual(json.loads(result.body)['content'], 'apirecallmarker')
        finally:
            store.close()
