from database.db_core import DatabaseCore


class SubstationRepository:
    def __init__(self, db: DatabaseCore | None = None) -> None:
        self.db = db or DatabaseCore()

    def get_all(self, limit: int | None = 500):
        sql = """
        SELECT
            Id,
            Name,
            Latitude,
            Longitude
        FROM Substations
        WHERE Latitude IS NOT NULL
          AND Longitude IS NOT NULL
        """
        if limit is not None:
            sql = sql.replace("SELECT", f"SELECT TOP {limit}", 1)
        return self.db.fetch_all(sql)


class TransmissionStationRepository:
    def __init__(self, db: DatabaseCore | None = None) -> None:
        self.db = db or DatabaseCore()

    def get_all(self, limit: int | None = 500):
        sql = """
        SELECT
            Id,
            Name,
            Latitude,
            Longitude
        FROM TransmissionStations
        WHERE Latitude IS NOT NULL
          AND Longitude IS NOT NULL
        """
        if limit is not None:
            sql = sql.replace("SELECT", f"SELECT TOP {limit}", 1)
        return self.db.fetch_all(sql)


class DistributionSubstationRepository:
    def __init__(self, db: DatabaseCore | None = None) -> None:
        self.db = db or DatabaseCore()

    def get_all(self, limit: int | None = 500):
        sql = """
        SELECT
            Id,
            Name,
            MeterId,
            Feeder11Id,
            Feeder33Id,
            NameplateRating,
            Latitude,
            Longitude
        FROM DistributionSubstation
        WHERE Latitude IS NOT NULL
          AND Longitude IS NOT NULL
        """
        if limit is not None:
            sql = sql.replace("SELECT", f"SELECT TOP {limit}", 1)
        return self.db.fetch_all(sql)

class Feeder33Repository:
    def __init__(self, db: DatabaseCore | None = None) -> None:
        self.db = db or DatabaseCore()

    def get_all(self, limit: int | None = 500):
        sql = """
        SELECT
            Id,
            Name,
            TsId,
            MeterId,
            NameplateRating
        FROM Feeders33
        """
        if limit is not None:
            sql = sql.replace("SELECT", f"SELECT TOP {limit}", 1)
        return self.db.fetch_all(sql)


class Feeder11Repository:
    def __init__(self, db: DatabaseCore | None = None) -> None:
        self.db = db or DatabaseCore()

    def get_all(self, limit: int | None = 500):
        sql = """
        SELECT
            Id,
            Name,
            SsId,
            TsId,
            Feeder33Id,
            MeterId,
            NameplateRating
        FROM Feeders11
        """
        if limit is not None:
            sql = sql.replace("SELECT", f"SELECT TOP {limit}", 1)
        return self.db.fetch_all(sql)


class Feeder33SubstationRepository:
    def __init__(self, db: DatabaseCore | None = None) -> None:
        self.db = db or DatabaseCore()

    def get_all(self, limit: int | None = 500):
        sql = """
        SELECT
            Feeders33Id,
            SubstationsId
        FROM Feeder33Substation
        """
        if limit is not None:
            sql = sql.replace("SELECT", f"SELECT TOP {limit}", 1)
        return self.db.fetch_all(sql)
