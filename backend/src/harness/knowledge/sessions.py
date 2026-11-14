"""Resumable owner conversations without rewriting historical message records."""
import time
from uuid import uuid4

DEFAULT_SESSION_ID = 'legacy'


def initialize_sessions(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS chat_sessions(
      id TEXT PRIMARY KEY,title TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS chat_message_sessions(message_id TEXT PRIMARY KEY,session_id TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_chat_session_messages ON chat_message_sessions(session_id,message_id);
    CREATE TRIGGER IF NOT EXISTS chat_message_removed AFTER DELETE ON messages BEGIN
      DELETE FROM chat_message_sessions WHERE message_id=old.id; END;
    ''')
    now = int(time.time()*1000)
    db.execute('INSERT OR IGNORE INTO chat_sessions VALUES (?,?,0,?,?)',
               (DEFAULT_SESSION_ID, 'Earlier conversations', now, now))
    # Unmapped legacy/Node-created rows belong to the earlier conversation.
    # Keep the original messages schema intact for migration compatibility.


def _title(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 100 or any(ord(c)<32 for c in value):
        raise ValueError('Conversation title must have 1-100 characters without control characters')
    return value.strip()


class ChatSessions:
    def __init__(self, store): self.store = store

    def get(self, identifier, include_count=True):
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 200:
            raise ValueError('Invalid conversation ID')
        row = self.store.db.execute('SELECT * FROM chat_sessions WHERE id=?', (identifier,)).fetchone()
        if not row: raise ValueError('Conversation not found')
        count = self.store.db.execute('''SELECT count(*) FROM messages m
            LEFT JOIN chat_message_sessions s ON s.message_id=m.id
            WHERE coalesce(s.session_id,?)=?''', (DEFAULT_SESSION_ID, identifier)).fetchone()[0] if include_count else 0
        return dict(id=row['id'], title=row['title'], archived=bool(row['archived']),
                    createdAt=row['created_at'], updatedAt=row['updated_at'], messageCount=count)

    def active(self, identifier):
        session = self.get(identifier, include_count=False)
        if session['archived']: raise ValueError('Restore the archived conversation before sending messages')
        return session

    def list(self, archived=False):
        rows = self.store.db.execute("""SELECT c.*,coalesce(x.message_count,0) AS message_count,
            max(c.updated_at,coalesce(x.last_message,0)) AS last_activity FROM chat_sessions c
            LEFT JOIN (SELECT coalesce(s.session_id,?) AS session_id,max(m.created_at) AS last_message,count(*) AS message_count
                FROM messages m LEFT JOIN chat_message_sessions s ON s.message_id=m.id
                GROUP BY coalesce(s.session_id,?)) x ON x.session_id=c.id
            WHERE c.archived=? ORDER BY last_activity DESC,c.rowid DESC""",
            (DEFAULT_SESSION_ID,DEFAULT_SESSION_ID,int(archived))).fetchall()
        return [dict(id=r['id'],title=r['title'],archived=bool(r['archived']),createdAt=r['created_at'],
                     updatedAt=r['last_activity'],messageCount=r['message_count']) for r in rows]

    def create(self, body):
        if not isinstance(body, dict) or set(body)-{'title'}: raise ValueError('Invalid conversation settings')
        title = _title(body.get('title', 'New conversation'))
        if self.store.db.execute('SELECT count(*) FROM chat_sessions').fetchone()[0] >= 1000:
            raise ValueError('Conversation limit reached (1000); existing conversations remain available')
        identifier = str(uuid4()); now = int(time.time()*1000)
        self.store.db.execute('INSERT INTO chat_sessions VALUES (?,?,0,?,?)', (identifier,title,now,now))
        return self.get(identifier)

    def update(self, identifier, body):
        session = self.get(identifier)
        if not isinstance(body, dict) or not body or set(body)-{'title','archived'}:
            raise ValueError('Invalid conversation update')
        title = _title(body['title']) if 'title' in body else session['title']
        archived = body.get('archived', session['archived'])
        if type(archived) is not bool: raise ValueError('Archived must be a boolean')
        self.store.db.execute('UPDATE chat_sessions SET title=?,archived=?,updated_at=? WHERE id=?',
                             (title,int(archived),int(time.time()*1000),identifier))
        return self.get(identifier)

    def messages(self, identifier, before=None, limit=50):
        session = self.get(identifier); parameters = [DEFAULT_SESSION_ID,identifier]
        condition = ''
        if before is not None:
            if not isinstance(before,str) or not 1<=len(before)<=200: raise ValueError('Invalid message cursor')
            cursor = self.store.db.execute('''SELECT m.created_at,m.rowid FROM messages m
                LEFT JOIN chat_message_sessions s ON s.message_id=m.id
                WHERE m.id=? AND coalesce(s.session_id,?)=?''', (before,DEFAULT_SESSION_ID,identifier)).fetchone()
            if not cursor: raise ValueError('Conversation message cursor not found; reload the conversation')
            condition = ' AND (m.created_at<? OR (m.created_at=? AND m.rowid<?))'
            parameters += [cursor['created_at'],cursor['created_at'],cursor['rowid']]
        limit = max(1,min(100,int(limit)))
        rows = self.store.db.execute('''SELECT m.* FROM messages m
            LEFT JOIN chat_message_sessions s ON s.message_id=m.id
            WHERE coalesce(s.session_id,?)=?''' + condition +
            ' ORDER BY m.created_at DESC,m.rowid DESC LIMIT ?', (*parameters,limit+1)).fetchall()
        more = len(rows)>limit
        messages = [dict(id=r['id'],role=r['role'],content=r['content'],runId=r['run_id'],createdAt=r['created_at'])
                    for r in reversed(rows[:limit])]
        return dict(session=session,messages=messages,hasMore=more,nextBefore=messages[0]['id'] if more else None)
