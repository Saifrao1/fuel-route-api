from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=255)
    end = serializers.CharField(max_length=255)

    def validate_start(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("start must not be empty")
        return value

    def validate_end(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("end must not be empty")
        return value
