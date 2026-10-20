"""Real stdio protocol test against an isolated temporary database; no user data touched."""
import json
import os
import sys
import tempfile
from pathlib import Path
import anyio
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters


def unpack(result):
    if result.is_error:
        raise AssertionError(str(result.content))
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


async def exercise(folder: Path, mode: str):
    db = folder / ('test-' + mode + '.db')
    env = dict(os.environ, MEMORYHUB_DB_PATH=str(db), PYTHONUTF8='1')
    params = StdioServerParameters(command=sys.executable,
                                  args=['-I','-m','memoryhub.mcp.server'],env=env,cwd=str(folder))
    with anyio.fail_after(45):
        async with Client(params,mode=mode,read_timeout_seconds=15) as client:
            listed = await client.list_tools()
            names = {t.name for t in listed.tools}
            assert names == {'memory_remember','memory_recall','memory_manage','memory_status'}, names
            status = unpack(await client.call_tool('memory_status',{}))
            assert status['db_path'] == str(db)
            data = {'content':'测试项目使用 GPU 1','topic':'测试项目/训练设备',
                    'source_text':'请记住：测试项目使用 GPU 1。','evidence':'测试项目使用 GPU 1',
                    'reason':'后续生成训练命令需要遵守','worth_remembering':True,'kind':'constraint'}
            stored = unpack(await client.call_tool('memory_remember',{'proposal':data}))
            assert stored['action'] == 'stored',stored
            old = stored['memory']
            recalled = unpack(await client.call_tool('memory_recall',{'query':'训练设备'}))
            assert recalled['memories'][0]['id'] == old['id'],recalled
            # Malformed evidence must fail at the tool boundary.
            bad = dict(data,evidence='not present in source',topic='bad')
            failure = await client.call_tool('memory_remember',{'proposal':bad})
            assert failure.is_error
            update = dict(data,content='测试项目改用 GPU 0',source_text='更正：测试项目改用 GPU 0。',
                          evidence='测试项目改用 GPU 0',action='replace',target_id=old['id'],
                          expected_revision=old['revision'])
            changed = unpack(await client.call_tool('memory_remember',{'proposal':update}))
            assert changed['action'] == 'replaced',changed
            historical = unpack(await client.call_tool('memory_recall',{'topic':old['topic'],'as_of':old['valid_from']}))
            assert historical['memories'][0]['id'] == old['id'],historical
        # Separate server process, same database, different lifecycle.
        async with Client(params,mode=mode,read_timeout_seconds=15) as client:
            recalled = unpack(await client.call_tool('memory_recall',{'query':'GPU'}))
            assert len(recalled['memories']) == 1,recalled
            current = recalled['memories'][0]
            assert current['content'] == '测试项目改用 GPU 0'
            detail = unpack(await client.call_tool('memory_manage',{'action':'inspect','memory_id':current['id']}))
            assert detail['memory']['supersedes'] == old['id']
    print('PASS:',mode,'stdio, tool schemas, admission, errors, revision history, restart persistence')


def main() -> int:
    try:
        with tempfile.TemporaryDirectory(prefix='MemoryHub test ') as tmp:
            for mode in ('legacy','auto'):
                anyio.run(exercise,Path(tmp),mode)
    except Exception as exc:
        print('FAIL:',str(exc),file=sys.stderr)
        return 1
    print('PASS: isolated MCP self-test; production database was not changed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
