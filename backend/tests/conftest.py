import os


# Unit tests must never inherit real model credentials from the developer .env.
os.environ["DIET_AI_PROVIDER"] = "rule"
os.environ["DIET_DISABLE_PERSISTENCE"] = "1"
