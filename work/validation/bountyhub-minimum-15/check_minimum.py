"""Three focused offline groups for the actual catalog module.

The optional submission-target validator is deliberately a fail-if-called
sentinel. These checks do not exercise that unrelated collaborator. Provider
transport is an in-memory response fixture; no service requests are made.
"""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
from unittest.mock import patch

source = Path(sys.argv[1])
module = types.ModuleType('concierge.submission_packet')
def unused(*args, **kwargs):
    raise AssertionError('submission-target mapping is outside these checks')
module.validate_submission_target = unused
sys.modules['concierge.submission_packet'] = module
spec = importlib.util.spec_from_file_location('catalog_under_check', source)
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


def listing(index, amount, status='PAID', **flags):
    row = dict(id=f'00000000-0000-0000-0000-{index:012d}', repositoryFullName='Example/Repo',
        issueNumber=index, htmlURL=f'https://github.com/Example/Repo/issues/{index}',
        title='Synthetic threshold fixture', issueState='open', assignmentType='OPEN',
        assignee=None, claimed=False, retracted=False, solved=False, isFrozen=False,
        deletedAt=None, totalAmount=amount,
        pledges=[dict(retracted=False,deletedAt=None,amount=amount,paymentStatus=status,isPaid=False)],
        claims=[])
    row.update(flags)
    return row

rows = [listing(1,'14.99'),listing(2,'15.00'),listing(3,'24.99'),listing(4,'25.00'),
        listing(5,'15.00','PROMISED'),listing(6,'100.00',solved=True)]

class Response:
    status_code=200
    headers={}
    def __init__(self,payload): self.payload=payload
    def json(self): return copy.deepcopy(self.payload)
    def close(self): pass
class Session:
    def __init__(self): self.urls=[]
    def get(self,url,**kwargs):
        self.urls.append(url)
        if url==catalog.API:
            return Response(dict(data=rows,hasNextPage=False))
        return Response(next(row for row in rows if url.endswith('/'+row['id'])))
    def __enter__(self): return self
    def __exit__(self,*args): pass

def numbers(selection): return [row['number'] for row in selection['targets']]

def cli(args):
    stdout,stderr=io.StringIO(),io.StringIO()
    with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
        result=catalog.main(args)
    return result,stdout.getvalue(),stderr.getvalue()

# 1. Public selector and collector default admission, plus retained safety filters.
session=Session()
report=catalog.fetch_catalog(session=session)
assert report['minimum_total_usd']=='15.00', report['minimum_total_usd']
assert numbers(report['shortlist'])==[2,3,4]
assert numbers(catalog.select_targets(report))==[2,3,4]
assert numbers(catalog.select_targets(report,include_promised=True))==[2,3,4,5]
assert len(session.urls)==5 and report['complete'] is True
print('PASS 1/3: default15 includes15.00/24.99; excludes14.99, promised-only and solved rows; five fixture GETs')

# 2. Explicit25 and saved observation scope are not silently widened by default15.
old=catalog.fetch_catalog(minimum_total_usd='25.00',session=Session())
old_copy=copy.deepcopy(old)
assert numbers(catalog.select_targets(report,'25.00'))==[4]
with patch.object(catalog.requests,'Session',side_effect=AssertionError('no-op must not connect')):
    resumed=catalog.resume_catalog(old)
assert old==old_copy
assert resumed['minimum_total_usd']=='25.00' and numbers(resumed['shortlist'])==[4]
assert resumed['completed_at']==old['completed_at'] and resumed['resume']['requests_made']==0
print('PASS 2/3: explicit25 and resumed25 scope/timestamps preserved; resume performs zero requests')

# 3. Actual argparse/CLI collect and targets agree, including explicit aliases/help.
with patch.object(catalog.requests,'Session',Session):
    code,text,_=cli(['collect'])
assert code==0 and json.loads(text)['minimum_total_usd']=='15.00'
with tempfile.TemporaryDirectory() as directory:
    snapshot=Path(directory)/'catalog.json'; snapshot.write_text(json.dumps(report))
    code,text,_=cli(['targets',str(snapshot)])
    assert code==0 and [r['number'] for r in json.loads(text)['candidates']]==[2,3,4]
    code,text,_=cli(['targets',str(snapshot),'--min-reward-usd','25.00'])
    assert code==0 and [r['number'] for r in json.loads(text)['candidates']]==[4]
for command in ('collect','targets'):
    out=io.StringIO()
    with contextlib.redirect_stdout(out):
        try: catalog.main([command,'--help'])
        except SystemExit as exc: assert exc.code==0
    assert 'default: 15.00' in out.getvalue()
print('PASS 3/3: real collect/targets CLI default/help15; explicit25 alias still works')
print('3 focused groups passed; zero live provider requests; no package-wide or mapping validation claimed')
