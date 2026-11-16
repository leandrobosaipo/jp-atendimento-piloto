import sqlite3
from capture_health import observe

def test_gap_and_deduplication():
    db=sqlite3.connect(':memory:')
    for t,ok in [(1000,True),(1060,False),(1120,False),(1180,False),(1240,False),(1300,False),(1360,True),(1420,True),(1660,True)]:
        observe(db,str(t),t,ok)
    assert db.execute('select start,kind from capture_incidents order by rowid').fetchall()==[(1060,'interrompido'),(1060,'retomado'),(1420,'interrompido'),(1420,'retomado')]
    observe(db,'1720',1720,False)
    observe(db,'1780',1780,True)
    assert db.execute('select count(*) from capture_incidents').fetchone()[0]==4
