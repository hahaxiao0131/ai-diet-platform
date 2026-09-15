"""RiskEngine module boundary."""

from .services import detect_profile_food_risks, detect_risks, food_allergen_context, recommendation_avoidances, safety_term_matches

__all__ = ["detect_profile_food_risks", "detect_risks", "food_allergen_context", "recommendation_avoidances", "safety_term_matches"]
