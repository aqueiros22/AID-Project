"""Generate the dimensional ER diagram directly from the SQLite schema."""
import sqlite3
import shutil
import subprocess
from scripts.paths import PROJECT_ROOT, SCHEMA, MEDIA


def diagram():
    conn = sqlite3.connect(':memory:')
    try:
        conn.executescript(SCHEMA.read_text(encoding='utf-8'))
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                  if r[0] != 'warehouse_metadata']
        lines = ['erDiagram', '    direction LR']
        relations = []
        for table in tables:
            columns = conn.execute(f'PRAGMA table_info({table})').fetchall()
            foreign = {r[3]: r[2] for r in conn.execute(f'PRAGMA foreign_key_list({table})')}
            unique = set()
            for index in conn.execute(f'PRAGMA index_list({table})').fetchall():
                if index[2] and index[3] != 'pk':
                    unique.update(r[2] for r in conn.execute(f'PRAGMA index_info({index[1]})'))
            lines.append(f'    {table} {{')
            for col in columns:
                name, kind, pk = col[1], col[2], col[5]
                flags = (["PK"] if pk else []) + (["FK"] if name in foreign else []) + (["UK"] if name in unique else [])
                suffix = ' ' + ', '.join(flags) if flags else ''
                lines.append(f'        {kind} {name}{suffix}')
                if name in foreign:
                    # Identifying if the FK participates in the child primary key.
                    connector = '||--o{' if pk else '||..o{'
                    relations.append(f'    {foreign[name]} {connector} {table} : "{name}"')
            lines.append('    }')
        return '\n'.join(lines + relations) + '\n'
    finally:
        conn.close()


def export_graphviz(content, formats=('svg', 'pdf')):
    """Optional standalone vector/PDF exports from the same schema-derived entities."""
    executable = shutil.which('dot')
    if not executable:
        print('Graphviz not installed: Mermaid generated; SVG/PDF export skipped.')
        return
    lines = ['digraph model {',
             'graph [rankdir=LR, ranksep=1.6, nodesep=0.6, bgcolor="white", pad=0.3, '
             'fontname="Arial", fontsize=12, labelloc=t, '
             'label="IMDb dimensional model\\nPK: primary key | FK: foreign key | UK: unique\\n'
             'Each link: one parent, zero or many children\\nSolid: FK in child PK | Dashed: FK outside child PK"];',
             'node [shape=plain, fontname="Arial", fontsize=11];',
             'edge [fontname="Arial", fontsize=9, color="#536574", arrowsize=0.6];']
    table = None
    rows = []
    parent_ports = set()
    for line in content.splitlines():
        tokens = line.strip().split()
        if len(tokens) == 2 and tokens[-1] == '{':
            table = tokens[0]
            color = '#12666b' if table.startswith('fact_') else '#355474'
            rows = [f'<TR><TD COLSPAN="3" BGCOLOR="{color}"><FONT COLOR="white"><B>{table}</B></FONT></TD></TR>',
                    '<TR><TD><B>Attribute</B></TD><TD><B>Type</B></TD><TD><B>Key</B></TD></TR>']
        elif table and tokens == ['}']:
            lines.append(f'{table} [label=<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="6">'
                         + ''.join(rows) + '</TABLE>>];')
            table = None
        elif table and tokens:
            kind, name, *flags = tokens
            rows.append(f'<TR><TD ALIGN="LEFT" PORT="{name}">{name}</TD><TD>{kind}</TD><TD PORT="{name}_out">{" ".join(flags)}</TD></TR>')
        elif len(tokens) >= 5 and tokens[1] in ('||..o{', '||--o{'):
            parent, connector, child = tokens[:3]
            key = tokens[-1].strip('"')
            style = 'solid' if connector == '||--o{' else 'dashed'
            tail_label = '' if (parent, key) in parent_ports else '1'
            parent_ports.add((parent, key))
            lines.append(f'{parent}:{key}_out:e -> {child}:{key}:w '
                         f'[taillabel="{tail_label}", headlabel="0..N", labeldistance=2, style={style}];')
    lines += ['{rank=same; fact_title_release; fact_series_year_snapshot; fact_title_rating_snapshot;}', '}']
    dot = '\n'.join(lines).encode('utf-8')
    for format in formats:
        subprocess.run([executable, '-T' + format, '-o', str(MEDIA / f'dimensional_model.{format}')],
                       input=dot, check=True, capture_output=True)


if __name__ == '__main__':
    content = diagram()
    (PROJECT_ROOT / 'docs/dimensional_model.mmd').write_text(content, encoding='utf-8')
    readme = PROJECT_ROOT / 'README.MD'
    text = readme.read_text(encoding='utf-8')
    start = text.index('```mermaid\n')
    end = text.index('```', start + len('```mermaid\n'))
    readme.write_text(text[:start] + '```mermaid\n' + content + text[end:], encoding='utf-8')
    export_graphviz(content)
    print('Updated README ER diagram and docs/dimensional_model.mmd from the SQL schema.')
