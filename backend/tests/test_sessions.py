"""Conversation migration, page boundaries, contextual scoping and API races."""
import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harness.persistence.store import LocalStore
from harness.knowledge.sessions import ChatSessions
from harness.knowledge.memory import build_context_packet
from tests.test_runtime import RuntimeTests
from tests.offline_loop import OfflineAsyncCase


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.store=LocalStore(':memory:'); self.chats=ChatSessions(self.store)
    def tearDown(self): self.store.close()

    def test_legacy_rows_keep_ids_and_schema_and_new_sessions_persist(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path=str(Path(directory)/'old.sqlite')
            db=sqlite3.connect(path)
            db.execute('CREATE TABLE messages(id TEXT PRIMARY KEY,role TEXT NOT NULL,content TEXT NOT NULL,run_id TEXT,created_at INTEGER NOT NULL)')
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?)',('old-id','user','Retained before sessions',None,1))
            db.commit(); db.close()
            store=LocalStore(path)
            try:
                chats=ChatSessions(store)
                self.assertEqual(chats.messages('legacy')['messages'][0]['id'],'old-id')
                session=chats.create({'title':'Project Orion'})
                store.add_message('user','New scoped message',session_id=session['id'])
                # A historical writer can still use the five-column schema.
                store.db.execute('INSERT INTO messages VALUES (?,?,?,?,?)',('raw-id','assistant','Raw legacy writer',None,2))
            finally: store.close()
            store=LocalStore(path)
            try:
                chats=ChatSessions(store)
                self.assertEqual([m['id'] for m in chats.messages('legacy')['messages']],['old-id','raw-id'])
                self.assertEqual(chats.messages(session['id'])['messages'][0]['content'],'New scoped message')
            finally: store.close()

    def test_keyset_pages_identical_timestamps_no_duplicates_or_cross_session_cursor(self):
        session=self.chats.create({})
        other=self.chats.create({'title':'Other'})
        ids=[]
        for i in range(131):
            m=self.store.add_message('user',f'Message {i}',session_id=session['id']); ids.append(m['id'])
            self.store.db.execute('UPDATE messages SET created_at=123 WHERE id=?',(m['id'],))
        seen=[]; cursor=None
        while True:
            page=self.chats.messages(session['id'],cursor)
            seen=[m['id'] for m in page['messages']]+seen
            if not page['hasMore']: break
            cursor=page['nextBefore']
        self.assertEqual(seen,ids)
        with self.assertRaisesRegex(ValueError,'cursor not found'): self.chats.messages(other['id'],ids[0])
        self.assertEqual(self.chats.get(session['id'])['messageCount'],131)
        self.store.db.execute('DELETE FROM messages WHERE id=?',(ids[0],))
        self.assertEqual(self.chats.get(session['id'])['messageCount'],130)
        self.assertIsNone(self.store.db.execute('SELECT * FROM chat_message_sessions WHERE message_id=?',(ids[0],)).fetchone())

    def test_archive_restore_validation_and_no_silent_message_relocation(self):
        session=self.chats.create({})
        self.store.add_message('user','First title\twith spaces',session_id=session['id'])
        self.assertEqual(self.chats.get(session['id'])['title'],'First title with spaces')
        self.chats.update(session['id'],{'title':'Renamed','archived':True})
        self.assertNotIn(session['id'],[s['id'] for s in self.chats.list()])
        self.assertEqual(self.chats.list(True)[0]['id'],session['id'])
        with self.assertRaisesRegex(ValueError,'Restore'): self.store.add_message('user','Rejected',session_id=session['id'])
        self.chats.update(session['id'],{'archived':False})
        self.assertEqual(len(self.chats.messages(session['id'])['messages']),1)
        self.assertEqual(self.chats.messages('legacy')['messages'],[])
        for body in ({'title':''},{'title':'x\ny'},{'archived':'false'},{'unknown':True}):
            with self.assertRaises(ValueError): self.chats.update(session['id'],body)

    def test_context_scopes_recent_and_summary_but_all_history_remains_searchable(self):
        from harness.knowledge.recall import RecallIndex
        session=self.chats.create({}); other=self.chats.create({})
        a=self.store.add_message('user','Selected current fact',session_id=session['id'])
        self.store.add_message('user','Unrelated zephyrotherchatmarker',session_id=other['id'])
        self.store.set_setting('conversationSummary',{'text':'Legacy summary only'})
        self.store.set_setting('conversationSummary:'+session['id'],{'text':'Scoped selected summary'})
        packet=build_context_packet(self.store,'selected',session['id'])
        self.assertIn('Scoped selected summary',packet['text'])
        self.assertNotIn('Legacy summary only',packet['text'])
        self.assertNotIn('zephyrotherchatmarker',packet['text'])
        self.assertIn(a['id'],[s['id'] for s in packet['sources']])
        self.assertEqual(packet['characters'],len(packet['text']))
        self.assertEqual(packet['modelCalls'],0)
        self.assertTrue(RecallIndex(self.store).search('zephyrotherchatmarker')['results'])

    def test_forget_redacts_conversation_titles_and_invalidates_session_summaries(self):
        phrase='private title claim'
        session=self.chats.create({'title':phrase})
        memory=self.store.save_memory({'text':phrase,'source':'user','provenance':'explicit'})
        self.store.set_setting('conversationSummary:'+session['id'],{'text':phrase})
        self.store.forget_memory(memory['id'])
        self.assertEqual(self.chats.get(session['id'])['title'],'[forgotten]')
        self.assertIsNone(self.store.get_setting('conversationSummary:'+session['id']))


class SessionRuntimeTests(OfflineAsyncCase):
    asyncSetUp=RuntimeTests.asyncSetUp
    asyncTearDown=RuntimeTests.asyncTearDown
    runner=RuntimeTests.runner
    request=RuntimeTests.request

    async def test_session_chat_stats_routes_and_template_execution(self):
        status,session=await self.request('/chat-sessions',{'title':'Resume me'})
        self.assertEqual(status,201)
        status,result=await self.request('/chat',{'message':'Session request','sessionId':session['id']})
        self.assertEqual(status,200,result)
        self.assertEqual(result['sessionId'],session['id'])
        self.assertLessEqual(result['context']['characters'],12000)
        self.assertEqual(len(self.runtime.chats.messages(session['id'])['messages']),2)
        self.assertEqual(self.runtime.chats.messages('legacy')['messages'],[])
        status,template=await self.request('/workflow-templates',{'name':'Reusable evidence team','specialists':[
            {'role':'Evidence researcher','prompt':'Find original sources'},
            {'role':'Skeptic','prompt':'Check counterexamples'}]})
        self.assertEqual(status,201,template)
        status,workflow=await self.request('/workflows',{'prompt':'Use the reviewed team','templateId':template['id']})
        self.assertEqual(status,202,workflow)
        self.assertEqual(workflow['nodes'][0]['role'],'Evidence researcher')
        self.assertEqual((await self.request('/workflow-templates/'+template['id'],method='DELETE'))[0],200)
        self.assertEqual(self.runtime.store.get_workflow(workflow['id'])['nodes'][0]['role'],'Evidence researcher')

    async def test_template_catalog_is_read_only_bounded_and_forget_removes_private_instructions(self):
        phrase='Private "Montréal" claim'
        template=self.runtime.templates.save({'name':'Sourced team','specialists':[
            {'role':'Researcher','prompt':phrase}, {'role':'Reviewer','prompt':'Verify sources'}]})
        catalogue=await self.runtime.operate('workflow_templates',{},read_only=True)
        self.assertNotIn('specialists',catalogue['templates'][0])
        assignment=await self.runtime.operate('workflow_templates',{'id':template['id'],'offset':0},read_only=True)
        self.assertEqual(len(assignment['specialists']),1)
        self.assertEqual(assignment['specialists'][0]['prompt'],phrase)
        self.assertEqual(self.runtime.store.list_tasks(),[])
        memory=self.runtime.store.save_memory({'text':phrase,'source':'user','provenance':'explicit'})
        self.runtime.store.forget_memory(memory['id'])
        self.assertEqual(self.runtime.templates.list(),[])

    async def test_inflight_chat_rejects_archive_and_parallel_send_without_losing_reply(self):
        session=self.runtime.chats.create({}); started=asyncio.Event(); release=asyncio.Event()
        async def runner(config,prompt,context,caps):
            started.set(); await release.wait(); caps.on_usage(5,5); return 'Preserved reply'
        self.runtime.runner=runner
        turn=asyncio.create_task(self.request('/chat',{'message':'In-flight request','sessionId':session['id']}))
        try:
            await started.wait()
            self.assertEqual((await self.request('/chat-sessions/'+session['id'],{'archived':True},'PATCH'))[0],409)
            self.assertEqual((await self.request('/chat',{'message':'Parallel request'}))[0],409)
        finally: release.set()
        status,result=await turn
        self.assertEqual(status,200,result)
        self.assertEqual(self.runtime.chats.messages(session['id'])['messages'][-1]['content'],'Preserved reply')
        self.assertFalse(self.runtime.chat_busy)

    async def test_compaction_uses_one_session_and_drops_stale_snapshot(self):
        session=self.runtime.chats.create({})
        for i in range(45): self.runtime.store.add_message('user',f'Selected {i}',session_id=session['id'])
        self.runtime.store.add_message('user','Not in this summary')
        async def runner(config,prompt,context,caps):
            self.assertNotIn('Not in this summary',context)
            caps.on_usage(5,5); return 'Scoped summary'
        self.runtime.runner=runner
        result=await self.runtime.compact_history(session['id'])
        self.assertTrue(result['compacted'])
        self.assertIsNone(self.runtime.store.get_setting('conversationSummary'))
        self.assertEqual(self.runtime.store.get_setting('conversationSummary:'+session['id'])['text'],'Scoped summary')
        self.runtime.store.add_message('user','Another selected turn',session_id=session['id'])
        async def corrected(config,prompt,context,caps):
            self.runtime.store.add_message('user','Concurrent selected turn',session_id=session['id'])
            caps.on_usage(5,5); return 'Stale summary'
        self.runtime.runner=corrected
        with self.assertRaisesRegex(ValueError,'changed during compaction'): await self.runtime.compact_history(session['id'])
        self.assertEqual(self.runtime.store.get_setting('conversationSummary:'+session['id'])['text'],'Scoped summary')


del RuntimeTests
