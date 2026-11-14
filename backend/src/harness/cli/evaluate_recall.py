"""Repeatable local keyword-recall evaluation using synthetic retained work only."""
import argparse
import json
from pathlib import Path

from harness.persistence.store import LocalStore
from harness.knowledge.memory import build_context_packet
from harness.knowledge.recall import RecallIndex


def evaluate():
    store=LocalStore(':memory:')
    try:
        cases=[]
        def conversation(content,query):
            message=store.add_message('assistant',content)
            cases.append((query,('message',message['id'])))
        conversation('Falcon launch notes: ship the onboarding guide after review.','Falcon launch notes')
        conversation('The Montréal bicycle route crosses the Lachine canal.','MONTRÉAL bicycle')
        conversation('Report artifact is saved at reports/helios-q4.csv.','helios q4 csv')
        conversation('The oldest Atlas migration decision uses SQLite checkpoints.','Atlas migration checkpoints')
        # Later messages must not hide the old decisions or dominate ranking.
        for i in range(1005):
            store.add_message('assistant',f'Routine status {i}: launch notes, report, migration and review still pending.')
        task=store.create_task({'prompt':'Review Zephyr budget','parentId':'synthetic-evaluation-team'})
        store.update_task(task['id'],{'status':'running'})
        store.update_task(task['id'],{'status':'completed','result':'Zephyr specialist verified the 42 unit budget.'})
        cases.append(('Zephyr verified budget',('task',task['id'])))
        memory=store.save_memory({'text':'Prefer quiet Kestrel meeting mornings.','kind':'preference','source':'user','provenance':'explicit'})
        cases.append(('Kestrel mornings',('memory',memory['id'])))
        event=store.append_event('mcp.result',{'tool':'lookup','result':'Nebula inventory returned seven blue widgets.'})
        cases.append(('Nebula inventory widgets',('tool',event['id'])))
        event=store.append_event('action.completed',{'result':'Approved Cirrus calendar event created successfully.'})
        cases.append(('Cirrus calendar created',('action',event['id'])))
        index=RecallIndex(store); ranks=[]; characters=[]; model_calls=0
        for query,expected in cases:
            result=index.search(query,limit=20); model_calls+=result['modelCalls']
            identities=[(r['kind'],r['id']) for r in result['results']]
            ranks.append(identities.index(expected)+1 if expected in identities else None)
            packet=build_context_packet(store,query)
            characters.append(packet['characters']); model_calls+=packet['modelCalls']
        return {'scope':'synthetic keyword retrieval; not semantic, live-model or cost evaluation',
                'cases':len(cases),'recallAt3':sum(r is not None and r<=3 for r in ranks)/len(cases),
                'meanReciprocalRank':sum(1/r if r else 0 for r in ranks)/len(cases),
                'maxContextCharacters':max(characters),'averageContextCharacters':round(sum(characters)/len(characters)),
                'contextCharacterLimit':12000,'modelCalls':model_calls,
                'queries':[{'query':case[0],'rank':rank} for case,rank in zip(cases,ranks)]}
    finally: store.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(); result=evaluate(); rendered=json.dumps(result,indent=2,ensure_ascii=False)
    if args.output: args.output.write_text(rendered+'\n',encoding='utf-8')
    print(rendered)
    if result['recallAt3']<0.875 or result['meanReciprocalRank']<0.75 or result['maxContextCharacters']>12000 or result['modelCalls']:
        raise SystemExit('Synthetic recall quality gate failed')


if __name__=='__main__': main()
