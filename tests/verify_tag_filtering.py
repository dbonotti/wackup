import os
import sys
import tempfile
import sqlite3
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db_manager

class TestTagFiltering(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_whatsapp.db")
        db_manager.init_db(self.db_path)

        with db_manager.db_connection(self.db_path, write=True) as conn:
            cur = conn.cursor()
            # Crear chats de prueba
            cur.execute("""
                INSERT INTO chats (chat_id, nombre, ultimo_mensaje, ultimo_tipo, ultimo_timestamp)
                VALUES ('5491112345678@c.us', 'Juan Perez', 'Hola como va', 'texto', 1700000000)
            """)
            cur.execute("""
                INSERT INTO chats (chat_id, nombre, ultimo_mensaje, ultimo_tipo, ultimo_timestamp)
                VALUES ('123456789@lid', '123456789@lid', 'Quiero un presupuesto', 'texto', 1700000100)
            """)
            cur.execute("""
                INSERT INTO chats (chat_id, nombre, ultimo_mensaje, ultimo_tipo, ultimo_timestamp)
                VALUES ('5491198765432@c.us', 'Maria Gomez', 'Gracias', 'texto', 1700000200)
            """)

            # Alias para el contacto @lid
            cur.execute("""
                INSERT INTO contact_aliases (alias_id, canonical_id)
                VALUES ('123456789@lid', '5491122334455@c.us')
            """)

            # Asignar etiquetas
            cur.execute("INSERT INTO crm_chat_tags (chat_id, tag, category) VALUES (?, ?, ?)",
                        ('5491112345678@c.us', 'VIP', 'sales'))
            cur.execute("INSERT INTO crm_chat_tags (chat_id, tag, category) VALUES (?, ?, ?)",
                        ('5491112345678@c.us', 'Mayorista', 'sales'))
            cur.execute("INSERT INTO crm_chat_tags (chat_id, tag, category) VALUES (?, ?, ?)",
                        ('5491122334455@c.us', 'Presupuesto', 'sales'))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_all_chats_tags_map(self):
        tags_map = db_manager.get_all_chats_tags_map(db_path=self.db_path)
        
        # Juan Perez debe tener VIP y Mayorista
        self.assertIn('5491112345678@c.us', tags_map)
        self.assertEqual(tags_map['5491112345678@c.us'], ['Mayorista', 'VIP'])

        # Tanto el canonical como el alias deben resolver la etiqueta Presupuesto
        self.assertIn('5491122334455@c.us', tags_map)
        self.assertIn('Presupuesto', tags_map['5491122334455@c.us'])

        self.assertIn('123456789@lid', tags_map)
        self.assertIn('Presupuesto', tags_map['123456789@lid'])

        # Maria Gomez no tiene etiquetas
        self.assertNotIn('5491198765432@c.us', tags_map)

    def test_list_all_tags(self):
        tags = db_manager.list_all_tags(db_path=self.db_path)
        tag_names = {t["tag"] for t in tags}
        self.assertIn("VIP", tag_names)
        self.assertIn("Mayorista", tag_names)
        self.assertIn("Presupuesto", tag_names)

if __name__ == '__main__':
    unittest.main()
