# main.py
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import re
import zlib
from typing import Dict, List, Tuple, Optional
from pydantic import BaseModel

app = FastAPI(title="PlantUML to SQL Converter")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class PlantUMLRequest(BaseModel):
    plantuml_code: str

class Entity:
    def __init__(self, name: str):
        self.name = name
        self.attrs = []  # (name, type, is_pk, is_fk, is_uk)
        self.pk = []
    
    def add_attr(self, name: str, type: str, is_pk=False, is_fk=False, is_uk=False):
        self.attrs.append((name, type, is_pk, is_fk, is_uk))
        if is_pk: self.pk.append(name)

class Relationship:
    def __init__(self, frm: str, to: str, type: str):
        self.frm = frm; self.to = to; self.type = type

class Parser:
    def __init__(self, code: str):
        self.code = code
        self.entities: Dict[str, Entity] = {}
        self.rels: List[Relationship] = []
        self.m2m = []
    
    def parse(self):
        cur = None
        for line in self.code.split('\n'):
            line = line.strip()
            if not line or line[0] == "'" or line.startswith('@'): continue
            
            m = re.match(r'entity\s+"?([^"{]+)"?\s+as\s+(\w+)\s*{', line) or \
                re.match(r'entity\s+(\w+)\s*{', line)
            if m:
                name = m.group(2) if len(m.groups()) > 1 else m.group(1)
                cur = Entity(name)
                self.entities[name] = cur
                continue
            
            if cur:
                m = re.match(r'\s*\+?(\w+)\s*:\s*(\w+).*?<<(.*?)>>', line)
                if m:
                    name, type, cons = m.groups()
                    cur.add_attr(name, type, 'PK' in cons, 'FK' in cons, 'UK' in cons)
            
            m = re.match(r'(\w+)\s*([\|}o][o\|]*--[o\|]*[\|o{]?)\s*(\w+)', line)
            if m and m.group(1) in self.entities and m.group(3) in self.entities:
                frm, type, to = m.groups()
                self.rels.append(Relationship(frm, to, type))
                if '}o--o{' in type: self.m2m.append((frm, to))
        return self.entities, self.rels, self.m2m

class SQLGen:
    def __init__(self, entities, rels, m2m):
        self.entities = entities; self.rels = rels; self.m2m = m2m
    
    def _type(self, t: str) -> str:
        return {'int':'INTEGER','string':'VARCHAR(255)','varchar':'VARCHAR(255)',
                'text':'TEXT','datetime':'TIMESTAMP','timestamp':'TIMESTAMP',
                'boolean':'BOOLEAN','bool':'BOOLEAN','enum':'VARCHAR(50)'}.get(t.lower(),'VARCHAR(255)')
    
    def _quote(self, n: str) -> str:
        return f'"{n}"' if n.lower() in {'user','group','table'} else n
    
    def _pk_col(self, name: str) -> Optional[str]:
        e = self.entities.get(name)
        return e.pk[0].lower() if e and len(e.pk) == 1 else None
    
    def _parent_child(self, r) -> Tuple[Optional[str], Optional[str]]:
        if r.type.startswith('||') and 'o|' in r.type: return r.frm, r.to
        if r.type.startswith('||') and 'o{' in r.type: return r.frm, r.to
        if 'o{' in r.type and r.type.endswith('||'): return r.to, r.frm
        if 'o|' in r.type and r.type.endswith('||'): return r.to, r.frm
        return None, None
    
    def generate(self) -> str:
        sql = ["-- PostgreSQL schema\n"]
        
        # many-to-many
        seen = set()
        for frm, to in self.m2m:
            key = tuple(sorted([frm, to]))
            if key in seen: continue
            seen.add(key)
            a, b = sorted([frm, to])
            sql.append(f"CREATE TABLE {a}_{b} (")
            sql.append(f"    {a}_id INTEGER NOT NULL,")
            sql.append(f"    {b}_id INTEGER NOT NULL,")
            sql.append(f"    joined_at TIMESTAMP DEFAULT NOW(),")
            sql.append(f"    PRIMARY KEY ({a}_id, {b}_id));\n")
        
        # tables
        for name, e in self.entities.items():
            if any(f"{a}_{b}" == name for a,b in [tuple(sorted(x)) for x in self.m2m]): continue
            sql.append(f"CREATE TABLE {self._quote(name)} (")
            cols = []
            for an, at, pk, fk, uk in e.attrs:
                cols.append(f"    {an} {self._type(at)} {'NOT NULL' if pk else 'NULL'}" + 
                           (" PRIMARY KEY" if pk and len(e.pk)==1 else "") +
                           (" UNIQUE" if uk else ""))
            if len(e.pk) > 1:
                cols.append(f"    PRIMARY KEY ({', '.join(e.pk)})")
            sql.append(",\n".join(cols))
            sql.append(");\n")
        
        # foreign keys
        sql.append("-- Foreign keys")
        done = set()
        
        # regular relations
        for r in self.rels:
            p, c = self._parent_child(r)
            if not p or not c: continue
            pk = self._pk_col(p)
            if not pk: continue
            
            if 'o|' in r.type:
                sql.append(f"\nALTER TABLE {c} ADD CONSTRAINT fk_{c}_{p}")
                sql.append(f"    FOREIGN KEY ({pk}) REFERENCES {p}({pk}) ON DELETE CASCADE;")
                done.add(f"{c}_{p}")
            elif 'o{' in r.type:
                fk_col = None
                for an,_,_,is_fk,_ in self.entities[c].attrs:
                    if is_fk and p.lower() in an.lower(): fk_col = an; break
                if not fk_col: fk_col = f"{p}_id"
                sql.append(f"\nALTER TABLE {c} ADD CONSTRAINT fk_{c}_{p}")
                sql.append(f"    FOREIGN KEY ({fk_col}) REFERENCES {p}({pk}) ON DELETE CASCADE;")
                done.add(f"{c}_{p}")
        
        # m2m fks
        for a,b in [tuple(sorted(x)) for x in self.m2m]:
            tbl = f"{a}_{b}"
            if f"{tbl}_{a}" not in done and self._pk_col(a):
                sql.append(f"\nALTER TABLE {tbl} ADD CONSTRAINT fk_{tbl}_{a}")
                sql.append(f"    FOREIGN KEY ({a}_id) REFERENCES {a}({self._pk_col(a)}) ON DELETE CASCADE;")
                done.add(f"{tbl}_{a}")
            if f"{tbl}_{b}" not in done and self._pk_col(b):
                sql.append(f"\nALTER TABLE {tbl} ADD CONSTRAINT fk_{tbl}_{b}")
                sql.append(f"    FOREIGN KEY ({b}_id) REFERENCES {b}({self._pk_col(b)}) ON DELETE CASCADE;")
                done.add(f"{tbl}_{b}")
        
        # participants special case
        if 'participants' in self.entities:
            sql.append("\nALTER TABLE participants ADD CONSTRAINT fk_participants_users")
            sql.append("    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;")
            sql.append("ALTER TABLE participants ADD CONSTRAINT fk_participants_rooms")
            sql.append("    FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE;")
        
        return "\n".join(sql)

def encode_plantuml(text: str) -> str:
    c = zlib.compress(text.encode())[2:-4]
    def e6(b): return chr(48+b) if b<10 else chr(65+b-10) if b<36 else chr(97+b-36) if b<62 else '-_'[b-62]
    def a3(b1,b2,b3): return ''.join(e6(x&0x3F) for x in [b1>>2, ((b1&3)<<4)|(b2>>4), ((b2&15)<<2)|(b3>>6), b3&63])
    res = ""
    for i in range(0, len(c), 3):
        b1,b2,b3 = c[i], c[i+1] if i+1<len(c) else 0, c[i+2] if i+2<len(c) else 0
        res += a3(b1,b2,b3)
    return res

# Minimal HTML template (just enough to work)
HTML = """
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>PlantUML to SQL</title>
<style>body{background:#1a1a1a;color:#eee;font-family:monospace;padding:20px}
.panels{display:grid;grid-template-columns:1fr 1.5fr 1fr;gap:20px;height:70vh}
.panel{background:#2e3440;border:1px solid #3b4252;border-radius:8px;display:flex;flex-direction:column}
.panel h3{background:#3b4252;margin:0;padding:10px;font-size:14px}
.panel-content{flex:1;overflow:hidden}
textarea{width:100%;height:100%;background:#1a1a1a;color:#a3be8c;border:none;padding:10px;font-family:monospace}
.sql{height:100%;overflow:auto;background:#1a1a1a;color:#a3be8c;padding:10px;white-space:pre}
.diagram{height:100%;background:white;display:flex;justify-content:center}
.buttons{display:flex;gap:10px;margin:10px 0;flex-wrap:wrap}
button{background:#434c5e;color:white;border:1px solid #4c566a;padding:8px 16px;border-radius:4px;cursor:pointer}
.status{background:#434c5e;padding:8px;margin-top:10px;text-align:right}
</style></head>
<body>
<h1>PlantUML → SQL (PostgreSQL)</h1>
<div class="buttons">
    <button id="ex1">Роли</button>
    <button id="ex2">Video (EN)</button>
    <button id="ex3">Video (RU)</button>
    <button id="render">🔄 Обновить</button>
    <button id="copy">📋 Копировать SQL</button>
    <button id="download">💾 Скачать SQL</button>
    <button id="clear">🗑️ Очистить</button>
</div>
<div class="panels">
    <div class="panel"><h3>📝 PlantUML</h3><div class="panel-content"><textarea id="input"></textarea></div></div>
    <div class="panel"><h3>🖼️ Diagram</h3><div class="panel-content"><div id="diagram" class="diagram">...</div></div></div>
    <div class="panel"><h3>🗄️ SQL</h3><div class="panel-content"><pre id="sql" class="sql">-- ready</pre></div></div>
</div>
<div id="status" class="status">Ready</div>

<script>
const EXAMPLES = {
    ex1: `@startuml
entity "Пользователь" as User { +id:int<<PK>> -- имя:string email:string }
entity "Постоянный" as Regular { +id:int<<PK,FK>> -- последний_визит:datetime }
User ||--o| Regular @enduml`,
    ex2: `@startuml
entity users { +id:int<<PK>> -- username:varchar email:varchar<<UK>> }
entity rooms { +id:int<<PK>> -- name:varchar creator_id:int<<FK>> }
entity participants { +user_id:int<<PK,FK>> +room_id:int<<PK,FK>> -- joined_at:timestamp }
users }o--o{ participants
rooms }o--o{ participants
@enduml`,
    ex3: `@startuml
entity "Пользователь" as User2 { +id:int<<PK>> -- имя:string email:string }
entity "Комната" as Room2 { +id:int<<PK>> -- название:string создатель_id:int<<FK>> }
User2 }o--o{ Room2 : участвует
@enduml`
};

const input = document.getElementById('input');
const sqlOut = document.getElementById('sql');
const diagram = document.getElementById('diagram');
const statusDiv = document.getElementById('status');

async function render() {
    if(!input.value.trim()) return;
    diagram.innerHTML = 'Loading...';
    const r = await fetch('/render', {method:'POST',body:JSON.stringify({plantuml_code:input.value}),
        headers:{'Content-Type':'application/json'}});
    const d = await r.json();
    if(r.ok) diagram.innerHTML = `<img src="${d.image_url}" style="max-width:100%">`;
    else diagram.innerHTML = `Error: ${d.detail}`;
}

async function convert() {
    if(!input.value.trim()) return;
    sqlOut.textContent = 'Converting...';
    const r = await fetch('/convert', {method:'POST',body:JSON.stringify({plantuml_code:input.value}),
        headers:{'Content-Type':'application/json'}});
    const d = await r.json();
    sqlOut.textContent = r.ok ? d.sql : `Error: ${d.detail}`;
    statusDiv.textContent = r.ok ? 'Converted' : 'Error';
}

document.getElementById('ex1').onclick = () => { input.value = EXAMPLES.ex1; render(); convert(); };
document.getElementById('ex2').onclick = () => { input.value = EXAMPLES.ex2; render(); convert(); };
document.getElementById('ex3').onclick = () => { input.value = EXAMPLES.ex3; render(); convert(); };
document.getElementById('render').onclick = render;
document.getElementById('copy').onclick = () => navigator.clipboard.writeText(sqlOut.textContent);
document.getElementById('download').onclick = () => {
    const b = new Blob([sqlOut.textContent], {type:'text/plain'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(b); a.download = 'schema.sql'; a.click();
};
document.getElementById('clear').onclick = () => { input.value = ''; sqlOut.textContent = '-- ready'; diagram.innerHTML = '...'; };

input.addEventListener('input', () => { setTimeout(render, 1000); setTimeout(convert, 1500); });
</script>
</body></html>"""

@app.get("/", response_class=HTMLResponse)
async def root(): return HTML

@app.post("/convert")
async def convert(req: PlantUMLRequest):
    try:
        e, r, m = Parser(req.plantuml_code).parse()
        return {"sql": SQLGen(e, r, m).generate()}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post("/render")
async def render(req: PlantUMLRequest):
    try:
        return {"image_url": f"https://www.plantuml.com/plantuml/png/{encode_plantuml(req.plantuml_code)}"}
    except Exception as e:
        raise HTTPException(400, str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)