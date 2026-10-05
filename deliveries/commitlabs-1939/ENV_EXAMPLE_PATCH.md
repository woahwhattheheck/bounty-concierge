# .env.example patch

Add this documentation near the existing feature/config JSON examples:

```dotenv
# Supported assets / risk profiles (server-side JSON override)
# Shape: {"assets":[{"code":"XLM","name":"Stellar Lumens","decimals":7}],"riskProfiles":[{"id":"conservative","name":"Conservative","description":"Strict capital preservation","maxLossBps":1000,"lockDurationDays":30}]}
# COMMITLABS_SUPPORTED_CONFIG_JSON=
```
