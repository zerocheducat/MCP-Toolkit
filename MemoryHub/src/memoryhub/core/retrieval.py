"""Portable lexical indexing; original identifiers and memory text stay intact."""
import math
import re

INDEX_VERSION = '1'
CANDIDATE_LIMIT = 256
MAX_QUERY_TERMS = 128


def text_terms(value: str) -> set[str]:
    words = set(re.findall(r'[a-z0-9_]+', value.casefold()))
    for run in re.findall(r'[\u3400-\u9fff]+', value):
        words.update(run[i:i+2] for i in range(len(run)-1))
        if len(run) == 1:
            words.add(run)
    return words


def index_memory(c, row):
    # Content/topic/namespace are immutable in the existing service: a
    # correction creates another ID. Status and validity are checked on the
    # authoritative memory row at retrieval time, never on a stale copy.
    if c.execute('SELECT 1 FROM mh_search_docs WHERE memory_id=?', (row['id'],)).fetchone():
        return
    terms = text_terms(row['content'] + ' ' + row['topic'])
    c.execute('INSERT INTO mh_search_docs(memory_id,namespace,term_count) VALUES (?,?,?)',
              (row['id'], row['namespace'], len(terms)))
    c.executemany('INSERT INTO mh_search_terms(term,memory_id) VALUES (?,?)',
                  ((term, row['id']) for term in terms))


def query_weights(c, namespace, terms):
    if not terms:
        return {}, set()
    slots = ','.join('?' for _ in terms)
    frequencies = {r['term']: r['n'] for r in c.execute(
        f'SELECT t.term,COUNT(*) n FROM mh_search_terms t JOIN mh_search_docs d '
        f'ON d.memory_id=t.memory_id WHERE d.namespace=? AND t.term IN ({slots}) GROUP BY t.term',
        [namespace, *terms])}
    # Compare frequencies only among terms that actually occur. An unknown
    # term must not make a real supporting match disappear.
    maximum = max(frequencies.values(), default=1)
    weights = {term: 1.0 + math.log((maximum + 1) / (frequencies.get(term, 0) + 1)) for term in terms}
    anchors = {term for term, count in frequencies.items() if count < maximum}
    return weights, anchors
