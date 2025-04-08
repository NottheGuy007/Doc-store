import streamlit as st
import sqlite3
from datetime import datetime
import hashlib
import magic
from pathlib import Path
import re
import os
import shutil

# Local storage setup
STORAGE_PATH = "document_storage"
Path(STORAGE_PATH).mkdir(exist_ok=True)
DB_PATH = "documents.db"

# Custom CSS for Notion-style UI
notion_css = """
<style>
body {
    font-family: 'Segoe UI', sans-serif;
}
.stApp {
    background-color: #fafafa;
    color: #2e2e2e;
}
.sidebar .sidebar-content {
    background-color: #f7f7f7;
    padding: 20px;
}
.stButton>button {
    background-color: #ffffff;
    border: 1px solid #e0e0e0;
    border-radius: 4px;
    padding: 8px 16px;
    color: #2e2e2e;
}
.stButton>button:hover {
    background-color: #f0f0f0;
}
.stTextInput>input, .stTextArea>textarea {
    border: 1px solid #e0e0e0;
    border-radius: 4px;
    background-color: #ffffff;
}
table {
    border-collapse: collapse;
    width: 100%;
}
th, td {
    padding: 12px;
    text-align: left;
    border-bottom: 1px solid #e0e0e0;
}
tr:hover {
    background-color: #f5f5f5;
}
.add-button {
    position: absolute;
    top: 10px;
    right: 10px;
}
</style>
"""
st.markdown(notion_css, unsafe_allow_html=True)

# Initialize database
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Documents (
            document_id TEXT PRIMARY KEY,
            title TEXT,
            notes TEXT,
            created_at TIMESTAMP,
            archived BOOLEAN
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Files (
            file_id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id TEXT,
            filename TEXT,
            filesize INTEGER,
            mimetype TEXT,
            sha256 TEXT,
            local_path TEXT,
            FOREIGN KEY (document_id) REFERENCES Documents (document_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Tags (
            document_id TEXT,
            tag TEXT,
            PRIMARY KEY (document_id, tag),
            FOREIGN KEY (document_id) REFERENCES Documents (document_id)
        )
    """)
    conn.commit()
    conn.close()

# Compute SHA256
def compute_sha256(file):
    sha256 = hashlib.sha256()
    for chunk in iter(lambda: file.read(4096), b""):
        sha256.update(chunk)
    file.seek(0)
    return sha256.hexdigest()

# Add document
def add_document(title, notes, tags, files):
    document_id = hashlib.md5(f"{title}{datetime.now()}".encode()).hexdigest()
    created_at = datetime.now()
    tags = [tag.strip() for tag in tags.split(",")] if tags else []

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO Documents (document_id, title, notes, created_at, archived) VALUES (?, ?, ?, ?, ?)",
        (document_id, title, notes, created_at, False)
    )

    mime = magic.Magic(mime=True)
    for file in files:
        filename = file.name
        sha256 = compute_sha256(file)
        filesize = len(file.read())
        file.seek(0)
        mimetype = mime.from_buffer(file.read(1024))
        file.seek(0)
        local_path = os.path.join(STORAGE_PATH, f"{document_id}_{filename}")
        with open(local_path, "wb") as f:
            shutil.copyfileobj(file, f)
        cursor.execute(
            "INSERT INTO Files (document_id, filename, filesize, mimetype, sha256, local_path) VALUES (?, ?, ?, ?, ?, ?)",
            (document_id, filename, filesize, mimetype, sha256, local_path)
        )
    for tag in tags:
        if tag:
            cursor.execute("INSERT OR IGNORE INTO Tags (document_id, tag) VALUES (?, ?)", (document_id, tag))
    conn.commit()
    conn.close()
    return document_id

# Parse search query (fixed syntax)
def parse_search_query(query):
    conditions = []
    params = []
    tags = []
    years = []
    mimes = []
    excludes = []

    tokens = re.split(r'\s+', query.strip())
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.startswith("tag:"):
            tags.append(token[4:])
        elif token.startswith("year:"):
            years.append(token[5:])
        elif token.startswith("mime:"):
            mimes.append(token[5:])
        elif token.startswith("-"):
            excludes.append(token[1:])
        elif token in ("AND", "OR"):
            conditions.append(token)
        else:
            conditions.append("(d.title LIKE ? OR d.notes LIKE ?)")
            params.extend([f"%{token}%", f"%{token}%"])
        i += 1

    sql = """
        SELECT d.document_id, d.title, d.notes, d.created_at, d.archived, 
               group_concat(t.tag) as tags, 
               group_concat(f.filename) as files, 
               group_concat(f.local_path) as local_paths
        FROM Documents d 
        LEFT JOIN Tags t ON d.document_id = t.document_id 
        LEFT JOIN Files f ON d.document_id = f.document_id
        WHERE 1=1
    """
    if tags:
        sql += " AND d.document_id IN (SELECT document_id FROM Tags WHERE " + " OR ".join(["tag = ?" for _ in tags]) + ")"
        params.extend(tags)
    if years:
        sql += " AND (" + " OR ".join(["strftime('%Y', d.created_at) = ?" for _ in years]) + ")"
        params.extend(years)
    if mimes:
        sql += " AND d.document_id IN (SELECT document_id FROM Files WHERE " + " OR ".join(["mimetype LIKE ?" for _ in mimes]) + ")"
        params.extend([f"%{m}%" for m in mimes])
    if excludes:
        sql += " AND (d.title NOT LIKE ? AND d.notes NOT LIKE ?)"
        params.extend([f"%{ex}%" for ex in excludes] * 2)
    if conditions:
        sql += " AND " + " ".join(conditions)
    sql += " GROUP BY d.document_id ORDER BY d.created_at DESC"
    return sql, params

# Search documents
def search_documents(query=""):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    sql, params = parse_search_query(query)
    cursor.execute(sql, params)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results

# Archive/unarchive document
def toggle_archive(document_id, archive=True):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE Documents SET archived = ? WHERE document_id = ?", (1 if archive else 0, document_id))
    conn.commit()
    conn.close()

# Streamlit app
st.title("DocStore")

# Add button in top-right corner
st.markdown('<div class="add-button">', unsafe_allow_html=True)
if st.button("Add Document"):
    st.session_state["page"] = "Add Document"
st.markdown('</div>', unsafe_allow_html=True)

# Sidebar
with st.sidebar:
    st.header("Workspace")
    page = st.selectbox("Navigate", ["Documents", "Tags"], format_func=lambda x: f"📋 {x}")
    st.markdown("---")
    st.write("Built with ❤️ by xAI")

# Default to Documents page if not set
if "page" not in st.session_state:
    st.session_state["page"] = "Documents"

# Add Document Page
if st.session_state.get("page") == "Add Document":
    st.header("New Document")
    with st.form("add_form"):
        title = st.text_input("Title", placeholder="Enter document title")
        notes = st.text_area("Notes", placeholder="Add some notes...")
        tags = st.text_input("Tags", placeholder="e.g., work, personal (comma-separated)")
        files = st.file_uploader("Upload Files", accept_multiple_files=True)
        submit = st.form_submit_button("Save")
        if submit and title and files:
            document_id = add_document(title, notes, tags, files)
            st.success(f"Document saved! ID: {document_id}")
            st.session_state["page"] = "Documents"  # Return to main page

# Documents Page (main page)
elif page == "Documents":
    st.header("Documents")
    query = st.text_input("Search", placeholder="e.g., tag:work year:2023 -draft")
    show_archived = st.checkbox("Show archived documents")
    results = search_documents(query)
    if not show_archived:
        results = [r for r in results if not r["archived"]]
    if results:
        for result in results:
            with st.expander(f"{result['title']} ({result['created_at'].strftime('%Y-%m-%d')})"):
                st.write(f"**Notes**: {result['notes'] or 'None'}")
                st.write(f"**Tags**: {result['tags'] or 'None'}")
                files = result["files"].split(",") if result["files"] else []
                local_paths = result["local_paths"].split(",") if result["local_paths"] else []
                for file, path in zip(files, local_paths):
                    with open(path, "rb") as f:
                        st.download_button(f"Download {file}", f, file_name=file)
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("Archive" if not result["archived"] else "Unarchive", key=f"toggle_{result['document_id']}"):
                        toggle_archive(result['document_id'], not result["archived"])
                        st.experimental_rerun()
                with col2:
                    if st.button("Open Files", key=f"open_{result['document_id']}"):
                        st.write("Opening files locally not supported in browser; download instead.")
    else:
        st.write("No documents uploaded yet.")

# Tags Page
elif page == "Tags":
    st.header("Tags")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT tag, COUNT(*) as count FROM Tags GROUP BY tag")
    tags = cursor.fetchall()
    conn.close()
    if tags:
        st.table({"Tag": [t[0] for t in tags], "Documents": [t[1] for t in tags]})
    else:
        st.write("No tags found.")

# Initialize DB
init_db()
