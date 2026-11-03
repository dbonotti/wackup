import os
import sys
import json
import urllib.request
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sse_hub
import config

class TestOperatorSelectionLan(unittest.TestCase):
    def setUp(self):
        self.hub = sse_hub.get_sse_hub()
        # Limpiar presencias previas
        with self.hub._presence_lock:
            self.hub._presence.clear()

    def test_online_operators_and_kick(self):
        # 1. Al inicio no hay operadores online
        online = self.hub.get_online_operators()
        self.assertEqual(online, [])

        # 2. Registrar presencia de 'Dario' y 'Ana'
        self.hub.record_presence("549111@c.us", "Dario", "client_1", source="visor")
        self.hub.record_presence("549222@c.us", "Ana", "client_2", source="whatsapp_web")

        online = self.hub.get_online_operators()
        self.assertIn("Dario", online)
        self.assertIn("Ana", online)

        # 3. 'Operador' genérico no debe contar como operador de equipo online
        self.hub.record_presence("549333@c.us", "Operador", "client_3", source="visor")
        online = self.hub.get_online_operators()
        self.assertNotIn("Operador", online)

        # 4. Kick a 'Dario'
        kicked = self.hub.kick_operator("Dario")
        self.assertTrue(kicked)

        online_after = self.hub.get_online_operators()
        self.assertNotIn("Dario", online_after)
        self.assertIn("Ana", online_after)

        # 5. Kick a operador no conectado
        kicked_fake = self.hub.kick_operator("Inexistente")
        self.assertFalse(kicked_fake)

if __name__ == '__main__':
    unittest.main()
