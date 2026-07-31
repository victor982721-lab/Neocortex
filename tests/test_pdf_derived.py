from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from _04_Nucleo_Operativo.pdf_derived import (
    PdfDerivedIndexer,
    initialize_derived_schema,
)


# region [01] Linear FTS/state reconciliation


class PdfDerivedRepairTests(unittest.TestCase):
    def test_layout_groups_stream_relations_in_two_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "pdf.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE documents(file_key TEXT PRIMARY KEY);
                CREATE TABLE pages(
                    file_key TEXT NOT NULL,page_number INTEGER NOT NULL,
                    PRIMARY KEY(file_key,page_number)
                ) WITHOUT ROWID;
                """
            )
            initialize_derived_schema(connection)
            connection.execute(
                "INSERT INTO similarity_state VALUES('layout','active',0.8,2,7,3)"
            )
            connection.executemany(
                "INSERT INTO similarity_relations VALUES(?,?,?,?,?,?,?)",
                (
                    (7, "a", "b", "layout_similar", 0.9, 2, "{}"),
                    (7, "b", "c", "layout_similar", 0.8, 2, "{}"),
                    (7, "d", "e", "layout_similar", 0.95, 2, "{}"),
                ),
            )
            connection.commit()
            connection.close()

            indexer = PdfDerivedIndexer(
                database,
                7,
                workers=1,
                similarity_threshold=0.8,
            )
            self.assertEqual(indexer._build_layout_groups(), 2)

            connection = sqlite3.connect(database)
            try:
                groups = connection.execute(
                    "SELECT representative_file_key,member_count,minimum_edge_score "
                    "FROM layout_groups ORDER BY member_count DESC"
                ).fetchall()
                member_count = connection.execute(
                    "SELECT COUNT(*) FROM layout_group_members"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(groups, [("b", 3, 0.8), ("d", 2, 0.95)])
            self.assertEqual(member_count, 5)

    def test_repairs_fts_and_state_without_losing_consistent_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "pdf.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE pages(
                    file_key TEXT NOT NULL,page_number INTEGER NOT NULL,
                    PRIMARY KEY(file_key,page_number)
                ) WITHOUT ROWID;
                CREATE VIRTUAL TABLE page_fts USING fts5(
                    file_key UNINDEXED,path UNINDEXED,page_number UNINDEXED,text
                );
                CREATE TABLE page_fts_state(
                    file_key TEXT NOT NULL,page_number INTEGER NOT NULL,
                    text_xxh3_128 TEXT NOT NULL,
                    PRIMARY KEY(file_key,page_number)
                ) WITHOUT ROWID;
                INSERT INTO pages VALUES('complete',0),('missing-fts',0),('missing-state',0);
                INSERT INTO page_fts(file_key,path,page_number,text) VALUES
                    ('complete','a.pdf',0,'a'),
                    ('missing-page','b.pdf',0,'b'),
                    ('missing-state','c.pdf',0,'c');
                INSERT INTO page_fts_state VALUES
                    ('complete',0,'a'),
                    ('missing-page',0,'b'),
                    ('missing-fts',0,'c');
                """
            )
            connection.commit()
            connection.close()

            indexer = object.__new__(PdfDerivedIndexer)
            indexer.state_path = database
            self.assertEqual(indexer._repair_fts_state(), 4)

            connection = sqlite3.connect(database)
            try:
                fts_keys = connection.execute(
                    "SELECT file_key,page_number FROM page_fts"
                ).fetchall()
                state_keys = connection.execute(
                    "SELECT file_key,page_number FROM page_fts_state"
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(fts_keys, [("complete", 0)])
            self.assertEqual(state_keys, [("complete", 0)])


# endregion [01]


if __name__ == "__main__":
    unittest.main()
