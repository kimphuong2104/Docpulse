# File: find_chunks.py
import os
import psycopg
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.environ["DATABASE_URL"]

keywords = {
    "q01": ["38 truyện", "Lời tựa", "Tuấn Khoa"],
    "q02": ["1939", "Tiểu thuyết thứ bảy"],
    "q03": ["83 truyện"],
    "q04": ["Linh Lan", "LAO ĐỘNG"],
    "q05": ["ngan", "đào"],
    "q06": ["Thoại", "Đào"],
    "q07": ["hồng", "quả to"],
    "q08": ["vó", "đầm"],
    "q09": ["khai trường", "giáo An", "Bính"],
    "q10": ["lá chanh", "hương nhu"],
}

with psycopg.connect(DATABASE_URL) as conn:
    with conn.cursor() as cur:
        for qid, kws in keywords.items():
            matched = []
            for kw in kws:
                cur.execute(
                    """
                    SELECT c.chunk_index
                    FROM chunks c
                    JOIN documents d ON d.id = c.document_id
                    WHERE d.filename = 'sample_report.pdf'
                      AND c.content ILIKE %s
                    ORDER BY c.chunk_index;
                    """,
                    (f"%{kw}%",),
                )
                matched.extend(r[0] for r in cur.fetchall())
            print(f"{qid}: {sorted(set(matched))}")