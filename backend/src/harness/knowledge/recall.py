"""Model-free retrieval over retained local work, with source-synchronized FTS."""
import re


# All SQL fragments are constants. User-supplied IDs and FTS terms are parameters.
SOURCES = {
    'message': ('messages', "role || ' conversation'", 'content', 'created_at', '1'),
    'task': ('tasks', 'prompt', "prompt || char(10) || status || char(10) || coalesce(result,'') || char(10) || coalesce(error,'')", 'created_at', '1'),
    'workflow': ('workflows', "coalesce(json_extract(definition,'$.prompt'),'Workflow plan')", 'definition', 'created_at', '1'),
    'memory': ('memories', "kind || ' memory'", 'text', 'created_at', '1'),
    'action': ('events', "'Completed action'", 'detail', 'created_at', "type='action.completed'"),
}
SOURCE_TAGS = {'message': 1, 'task': 2, 'workflow': 3, 'memory': 4, 'action': 5}


def initialize_recall(db):
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS recall_index USING fts5(kind UNINDEXED,source_id UNINDEXED,title,content,created_at UNINDEXED,tokenize='unicode61')")
    for kind, (table, title, content, created, condition) in SOURCES.items():
        # Stable tags partition FTS rowids across tables. Direct rowid lookups
        # avoid scanning every retained document on each update/backfill row.
        tag = SOURCE_TAGS[kind]
        # A view centralizes source projections so updates and reads use identical text.
        db.execute(f'CREATE VIEW IF NOT EXISTS recall_source_{kind} AS SELECT rowid AS source_rowid,id,{title} AS title,{content} AS content,{created} AS created_at FROM {table} WHERE {condition}')
        db.executescript(f'''
        CREATE TRIGGER IF NOT EXISTS recall_{kind}_added AFTER INSERT ON {table} BEGIN
          INSERT INTO recall_index(rowid,kind,source_id,title,content,created_at)
          SELECT source_rowid*8+{tag},'{kind}',id,title,content,created_at FROM recall_source_{kind} WHERE id=new.id; END;
        CREATE TRIGGER IF NOT EXISTS recall_{kind}_changed AFTER UPDATE ON {table} BEGIN
          DELETE FROM recall_index WHERE rowid=old.rowid*8+{tag};
          INSERT INTO recall_index(rowid,kind,source_id,title,content,created_at)
          SELECT source_rowid*8+{tag},'{kind}',id,title,content,created_at FROM recall_source_{kind} WHERE id=new.id; END;
        CREATE TRIGGER IF NOT EXISTS recall_{kind}_removed AFTER DELETE ON {table} BEGIN
          DELETE FROM recall_index WHERE rowid=old.rowid*8+{tag}; END;
        INSERT INTO recall_index(rowid,kind,source_id,title,content,created_at)
          SELECT s.source_rowid*8+{tag},'{kind}',s.id,s.title,s.content,s.created_at FROM recall_source_{kind} s
          WHERE NOT EXISTS(SELECT 1 FROM recall_index i WHERE i.rowid=s.source_rowid*8+{tag});
        ''')


class RecallIndex:
    def __init__(self, store):
        self.store = store

    def search(self, query, limit=12):
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ValueError('Query must have 1-1000 characters')
        terms = list(dict.fromkeys(t.lower() for t in re.findall(r'\w+', query, re.UNICODE) if len(t) > 1))[:30]
        if not terms:
            return {'results': [], 'modelCalls': 0}
        expression = ' OR '.join('"' + term + '"' for term in terms)
        rows = self.store.db.execute('''SELECT kind,source_id,title,created_at,
            snippet(recall_index,3,'[',']',' … ',48) AS excerpt
            FROM recall_index WHERE recall_index MATCH ? ORDER BY bm25(recall_index),created_at DESC LIMIT ?''',
            (expression, max(1, min(20, int(limit))))).fetchall()
        return {'results': [dict(id=r['source_id'], kind=r['kind'], title=r['title'][:160],
                                excerpt=r['excerpt'][:600], createdAt=int(r['created_at'])) for r in rows], 'modelCalls': 0}

    def read(self, kind, id_):
        if not isinstance(kind, str) or kind not in SOURCES or not isinstance(id_, str) or not 1 <= len(id_) <= 200:
            raise ValueError('Supply a valid recall kind and source ID')
        row = self.store.db.execute(f'SELECT * FROM recall_source_{kind} WHERE id=?', (id_,)).fetchone()
        if not row:
            raise ValueError('Retained source not found')
        return dict(id=row['id'], kind=kind, title=row['title'][:160], content=row['content'][:12000],
                    createdAt=int(row['created_at']), truncated=len(row['content']) > 12000)
