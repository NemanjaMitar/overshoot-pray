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
            Latitude,
            Longitude
        FROM DistributionSubstation
        WHERE Latitude IS NOT NULL
          AND Longitude IS NOT NULL
        """
        if limit is not None:
            sql = sql.replace("SELECT", f"SELECT TOP {limit}", 1)
        return self.db.fetch_all(sql)