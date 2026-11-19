import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import maintenance_triage as triage


class BookingChanges(unittest.TestCase):
    def test_same_day_booking_and_reschedule(self):
        today = dt.datetime.now(ZoneInfo("America/Cuiaba")).date()
        future = []
        directory = [{"Codigo": str(i), "Nome": f"Fixture {i}"} for i in range(101)]
        history = [{"CodCliente": "5", "DataIni": (today-dt.timedelta(days=20)).strftime("%d/%m/%Y"),
                    "Status": "title='Realizado'", "Servico": "Manutenção de prótese"}]

        def booking(customer, appointment, days):
            return dict(CliCodigo=customer, Age_Codigo=appointment, CIt_Codigo=appointment,
                        Ser_Codigo="1", ProCodigo="2", Ser_Descricao="Manutenção de prótese",
                        Age_Dat_Inicio=(today+dt.timedelta(days=days)).strftime("%d/%m/%Y 09:00"))

        def request(path, params):
            data = future if "Lista" in path else directory if "Clientes" in path else history
            return data, "fixture"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scope = root/"scope.json"
            scope.write_text(json.dumps({"held": ["1", "2", "3", "4"]}))
            with patch.multiple(triage, ROOT=root, SETTINGS=scope, _request=request, _consents=lambda: {}):
                future.append(booking("8","10",3))
                self.assertEqual(triage.run("1")["candidates"],1)
                self.assertFalse(triage.run("2")["refreshed"])
                future.extend([booking("5","11",3),booking("5","12",4)])
                stats=triage.run("3")
                self.assertEqual((stats["candidates"],stats["nearby_maintenance_pairs"]),(0,1))
                future.pop()
                self.assertEqual(triage.run("4")["nearby_maintenance_pairs"],0)
                saved=json.loads((root/"cohort-maintenance-15-90-n8n.json").read_text())
                self.assertEqual(saved["held"],["1","2","3","4"])


if __name__ == "__main__":
    unittest.main()
