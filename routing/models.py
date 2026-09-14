from django.db import models


class FuelStation(models.Model):
    """Truck stop with retail diesel price. Coordinates may be filled later."""

    opis_id = models.IntegerField(db_index=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=128, db_index=True)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.IntegerField(null=True, blank=True)
    retail_price = models.DecimalField(max_digits=8, decimal_places=4)

    # Optional; populated by geocoding or left null for city/state corridor match
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["state", "city"]),
            models.Index(fields=["retail_price"]),
        ]
        ordering = ["retail_price"]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) @ ${self.retail_price}"


class GeocodeCache(models.Model):
    """Persistent geocode results so we avoid hammering Nominatim."""

    query = models.CharField(max_length=512, unique=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    display_name = models.CharField(max_length=512, blank=True)
    country_code = models.CharField(max_length=8, blank=True)
    found = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        status = "hit" if self.found else "miss"
        return f"{self.query} [{status}]"
