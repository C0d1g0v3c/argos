from argos_ingest.freeze_cohort import composition_hash


def test_hash_es_estable_ante_orden():
    assert composition_hash(["b", "a", "c"]) == composition_hash(["a", "b", "c"])


def test_hash_cambia_con_la_composicion():
    assert composition_hash(["a", "b"]) != composition_hash(["a", "b", "c"])
