import { resolveApiBaseUrl } from "./apiBase.ts";

const API_BASE = resolveApiBaseUrl(import.meta.env.VITE_API_BASE_URL);

let accessToken = window.localStorage.getItem("diet_access_token") ?? "";

export function setAccessToken(value: string) {
  accessToken = value;
  if (value) window.localStorage.setItem("diet_access_token", value);
  else window.localStorage.removeItem("diet_access_token");
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...(options.headers ?? {}),
    },
  });
  const payload = await response.json();
  if (!response.ok) {
    const detail = payload?.detail;
    const message = typeof detail === "string"
      ? detail
      : detail?.message
        ?? (Array.isArray(detail) ? detail.map((item) => item?.msg).filter(Boolean).join("；") : "");
    throw new ApiError(message || "请求失败，请稍后重试", detail);
  }
  return Object.prototype.hasOwnProperty.call(payload, "data") ? payload.data : payload;
}

export class ApiError extends Error {
  detail: { code?: string; current_action?: AIAction } | undefined;

  constructor(message: string, detail?: { code?: string; current_action?: AIAction }) {
    super(message);
    this.name = "ApiError";
    this.detail = detail;
  }
}

export const api = {
  restoreSession: () => request<AuthSession>("/auth/session"),
  requestPhoneCode: (phone: string) => request<{ expires_in: number; message: string; dev_code?: string }>("/auth/phone/code", { method: "POST", body: JSON.stringify({ phone }) }),
  phoneLogin: (phone: string, code: string) => request<AuthSession>("/auth/phone/login", { method: "POST", body: JSON.stringify({ phone, code }) }),
  wechatLogin: (code: string) => request<AuthSession>("/auth/wechat/login", { method: "POST", body: JSON.stringify({ code }) }),
  logout: () => request<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  exportAccountData: () => request<AccountExport>("/account/export"),
  deleteAccount: (confirmation: string) => request<{ deleted: boolean; counts: Record<string, number> }>("/account", { method: "DELETE", body: JSON.stringify({ confirmation }) }),
  getProfile: () => request<Profile>("/profiles/me"),
  updateProfile: (payload: Partial<Profile>) =>
    request<Profile>("/profiles/me", { method: "PUT", body: JSON.stringify(payload) }),
  recalculateGoal: () => request<GoalProposal>("/goals/recalculate", { method: "POST" }),
  confirmGoal: (id: string) => request<GoalProposal>(`/goals/${id}/confirm`, { method: "POST" }),
  getGoal: () => request<GoalProposal | null>("/goals/current"),
  getToday: () => request<TodaySummary>("/today"),
  searchFoods: (query: string) => request<Food[]>(`/foods/search?q=${encodeURIComponent(query)}`),
  getRecentFoods: () => request<Food[]>("/foods/recent"),
  createCustomFood: (payload: CustomFoodCreatePayload) =>
    request<Food>("/foods/custom", { method: "POST", body: JSON.stringify(payload) }),
  lookupBarcode: (barcode: string) => request<Food>(`/foods/barcode/${encodeURIComponent(barcode)}`),
  createLabelFood: (payload: PackagedFoodLabelPayload) =>
    request<Food>("/foods/label", { method: "POST", body: JSON.stringify(payload) }),
  createManualMeal: (payload: ManualMealCreatePayload) =>
    request<Meal>("/meals/manual", { method: "POST", body: JSON.stringify(payload) }),
  saveMealFeedback: (mealId: string, payload: BodyFeedbackInput) =>
    request<Meal>(`/meals/${mealId}/feedback`, { method: "POST", body: JSON.stringify(payload) }),
  getSevenDayTrend: () => request<SevenDayTrend>("/trends/7-days"),
  assessDiet: (payload: DietAssessmentRequest) =>
    request<DietAssessment>("/ai/assess", { method: "POST", body: JSON.stringify(payload) }),
  createExampleDraft: (mealType: string) =>
    request<MealDraft>("/meal-drafts/example", {
      method: "POST",
      body: JSON.stringify({ meal_type: mealType }),
    }),
  confirmDraft: (draft: MealDraft, items: MealDraftItem[] = draft.items) =>
    request<Meal>("/meal-drafts/" + draft.id + "/confirm", {
      method: "POST",
      body: JSON.stringify({
        items: items.map((item) => ({
          item_id: item.id,
          food_id: item.food_id,
          name: item.name,
          weight_g: item.estimated_weight_g,
          consumed_ratio: item.consumed_ratio,
        })),
      }),
    }),
  previewPlans: () =>
    request<{ plans: MealPlan[]; remaining_before: Nutrition }>("/meal-plans/preview", {
      method: "POST",
      body: JSON.stringify({ meal_type: "dinner", scenario: "quick" }),
    }),
  savePlan: (id: string) => request<MealPlan>(`/meal-plans/${id}/save`, { method: "POST" }),
  replacePlan: (id: string, index: number, foodName: string) =>
    request<MealPlan>(`/meal-plans/${id}/replace`, {
      method: "POST",
      body: JSON.stringify({ index, food_name: foodName }),
    }),
  chat: (message: string, replyToTraceId?: string | null) =>
    request<AIChatResponse>("/ai/chat", {
      method: "POST",
      body: JSON.stringify({ message, reply_to_trace_id: replyToTraceId ?? null }),
    }),
  getAiStatus: () => request<AIServiceStatus>("/ai/status"),
  getAiHistory: (limit = 30, cursor?: string | null) => request<AIHistoryPage>(`/ai/history?limit=${limit}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`),
  getAiContext: () => request<AgentContext>("/ai/context"),
  getAiMemories: () => request<AgentMemory[]>("/ai/memories"),
  deleteAiMemory: (id: string) => request<AgentMemory>(`/ai/memories/${id}`, { method: "DELETE" }),
  giveAiFeedback: (traceId: string, rating: "helpful" | "not_helpful") =>
    request<AgentFeedback>(`/ai/traces/${traceId}/feedback`, { method: "POST", body: JSON.stringify({ rating }) }),
  confirmAiAction: (id: string, expectedRevision: number) =>
    request<{ action: AIAction; meal: Meal | null; exercise: ExerciseRecord | null; memory: AgentMemory | null; profile?: Profile | null }>(`/ai/actions/${id}/confirm`, { method: "POST", body: JSON.stringify({ expected_revision: expectedRevision }) }),
  updateAiAction: (id: string, payload: AIActionMealUpdatePayload) =>
    request<AIAction>(`/ai/actions/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  cancelAiAction: (id: string, expectedRevision: number) =>
    request<AIAction>(`/ai/actions/${id}/cancel`, { method: "POST", body: JSON.stringify({ expected_revision: expectedRevision }) }),
};

export type AuthSession = {
  access_token?: string;
  user: { id: string; is_new: boolean };
};

export type AccountExport = {
  schema_version: string;
  exported_at: string;
  profile: Profile;
  goals: GoalProposal[];
  active_goal: GoalProposal | null;
  weight_records: unknown[];
  meals: Meal[];
  exercises?: ExerciseRecord[];
  meal_drafts: MealDraft[];
  meal_plans: MealPlan[];
  custom_foods: Food[];
  ai_memories: AgentMemory[];
  ai_traces: Record<string, unknown>[];
  ai_actions: AIAction[];
  ai_feedback: AgentFeedback[];
};

export type Profile = {
  id: string;
  user_id: string;
  sex: "male" | "female" | "unknown" | null;
  age: number | null;
  height_cm: number | null;
  current_weight_kg: number | null;
  activity_level: "sedentary" | "light" | "moderate" | "high" | null;
  primary_goal: "fat_loss" | "muscle_gain" | "maintain" | "structure" | null;
  goal_pace: "steady" | "standard" | "aggressive" | null;
  allergies: string[];
  hard_exclusions: string[];
  preferences: string[];
  food_intolerances: string[];
  digestive_sensitivities: string[];
  exercise_habits: string[];
  safety_profile_reviewed: boolean;
  onboarding_completed: boolean;
};

export type Nutrition = {
  energy_kcal: number;
  protein_g: number;
  fat_g: number;
  carbs_g: number;
  fiber_g: number;
  sodium_mg: number;
  sugars_g: number | null;
  added_sugar_g: number | null;
  vegetable_g: number;
  fruit_g: number;
};

export type Food = {
  id: string;
  name: string;
  aliases: string[];
  food_type: "ingredient" | "standard_dish" | "packaged" | "custom";
  default_unit: string;
  default_weight_g: number | null;
  nutrition_per_100g: Nutrition;
  confidence: string;
  source: string;
  source_version: string;
  source_url: string | null;
  source_observed_at: string | null;
  barcode: string | null;
  brand: string | null;
  verified_by_user: boolean;
  allergens: string[];
};

export type CustomFoodCreatePayload = {
  name: string;
  basis_weight_g: number;
  default_weight_g?: number;
  nutrition: Nutrition;
  allergens?: string[];
};

export type PackagedFoodLabelPayload = CustomFoodCreatePayload & {
  barcode?: string;
  brand?: string;
};

export type ManualMealCreatePayload = {
  meal_type: "breakfast" | "lunch" | "dinner" | "snack";
  items: {
    food_id: string;
    weight_g: number;
    consumed_ratio: number;
  }[];
  body_feedback?: BodyFeedbackInput | null;
};

export type BodyFeedbackInput = {
  symptoms: string[];
  severity: "none" | "mild" | "moderate" | "severe";
  occurred_after_minutes?: number | null;
  notes?: string | null;
};

export type BodyFeedback = BodyFeedbackInput & {
  recorded_at: string;
};

export type GoalProposal = {
  id: string;
  target: Nutrition;
  reasons: string[];
  goal_type: string;
  pace: string;
  status: string;
};

export type MealDraftItem = {
  id: string;
  food_id: string;
  name: string;
  estimated_weight_g: number;
  household_unit: string;
  consumed_ratio: number;
  confidence: string;
};

export type MealDraft = {
  id: string;
  meal_type: MealType;
  items: MealDraftItem[];
  confidence: string;
};

export type Meal = {
  id: string;
  meal_type: string;
  items: { food_id: string | null; name: string; weight_g: number; consumed_ratio: number; nutrition: Nutrition; estimated_weight_range_g: number[] | null }[];
  nutrition: Nutrition;
  score: { score: number; negative_points: string[]; next_actions: string[] };
  risks: SafetyRisk[];
  confidence: string;
  body_feedback: BodyFeedback | null;
};

export type SafetyRisk = {
  level: string;
  evaluation_mode?: "record" | "recommendation";
  decision_effect?: "recorded_with_warning" | "blocked" | "notice";
  type?: string;
  restriction?: string;
  food_name?: string;
  message: string;
  alternative?: string;
};

export type TrendDay = {
  date: string;
  meal_count: number;
  score: number | null;
  food_variety: number;
  body_feedback_count: number;
  discomfort_count: number;
  symptoms: string[];
};

export type ReactionInsight = {
  food_name: string;
  symptom: string;
  occurrences: number;
  evidence_level: "observation" | "suspected" | "repeated";
  confidence: AgentConfidence;
  first_seen: string;
  last_seen: string;
  summary: string;
  next_action: string;
};

export type SevenDayTrend = {
  start_date: string;
  end_date: string;
  days: TrendDay[];
  average_score: number | null;
  total_meals: number;
  food_variety: number;
  feedback_completion_rate: number;
  common_symptoms: string[];
  reaction_insights: ReactionInsight[];
  feedback_due_count: number;
  focus_action: string | null;
};

export type DietAssessmentRequest = {
  meal_id?: string | null;
  days?: number;
  message?: string | null;
};

export type DietAssessment = {
  overall_score: number;
  general_analysis: string[];
  personalized_analysis: string[];
  optimization_suggestions: string[];
  nutrition_estimates: { nutrient: string; minimum: number; maximum: number; unit: string; basis: string }[];
  evidence: string[];
  confidence: AgentConfidence;
  disclaimer: string;
  markdown: string;
  provider: string;
  model: string | null;
  fallback_used: boolean;
  fallback_reason: string | null;
};

export type MealPlan = {
  id: string;
  title: string;
  scenario: string;
  items: { food_id: string | null; name: string; weight_g: number; nutrition: Nutrition }[];
  nutrition: Nutrition;
  reason: string;
  risks: { level: string; message: string }[];
  status: "draft" | "saved" | "converted" | "cancelled";
};

export type MealType = "breakfast" | "lunch" | "dinner" | "snack";

export type TodaySummary = {
  date: string;
  status: "empty" | "partial" | "complete";
  score: number | null;
  score_status: string;
  confidence: string;
  completeness: { recorded_meals: number; expected_meals: number };
  consumed: Nutrition;
  target: Nutrition;
  remaining: Nutrition;
  gaps: string[];
  near_limits: string[];
  meals: Meal[];
  exercises: ExerciseRecord[];
  exercise_count: number;
  exercise_duration_min: number;
  exercise_calories_burned: number;
  net_energy_kcal: number | null;
};

export type ExerciseRecord = {
  id: string;
  profile_id: string;
  exercise_type: string;
  started_at: string;
  duration_min: number;
  intensity: "low" | "moderate" | "high";
  calories_burned: number | null;
  calories_range_kcal: number[] | null;
  calories_source: "user_estimated" | "system_estimated";
  source: "manual" | "ai";
  notes: string | null;
  status: "active" | "deleted";
  created_at: string;
};

export type AIAction = {
  id: string;
  action_type: "create_meal" | "create_exercise" | "remember_preference" | "update_safety_profile";
  title: string;
  summary: string;
  payload: {
    meal_type?: "breakfast" | "lunch" | "dinner" | "snack";
    items?: {
      food_id: string;
      name: string;
      weight_g: number;
      consumed_ratio: number;
      estimated_weight_range_g?: number[] | null;
      data_source?: string;
      source_url?: string | null;
      brand?: string | null;
      confidence?: AgentConfidence;
    }[];
    body_feedback?: BodyFeedbackInput | null;
    eaten_at?: string;
    meal_id?: string;
    memory_id?: string;
    category?: "preference" | "avoidance" | "habit";
    profile_field?: "allergies" | "hard_exclusions" | "food_intolerances" | "digestive_sensitivities";
    operation?: "add" | "remove";
    value?: string;
    safety_risks?: SafetyRisk[];
    safety_acknowledged_at?: string;
    exercise_type?: string;
    started_at?: string;
    duration_min?: number;
    intensity?: "low" | "moderate" | "high";
    calories_burned?: number;
    calories_range_kcal?: number[] | null;
    calories_source?: "user_estimated" | "system_estimated";
    exercise_id?: string;
  };
  preview_nutrition: Nutrition;
  confidence: AgentConfidence;
  assumptions: string[];
  source_trace_id: string | null;
  status: "proposed" | "confirmed" | "cancelled";
  revision: number;
};

export type AIActionMealUpdatePayload = {
  meal_type: "breakfast" | "lunch" | "dinner" | "snack";
  items: {
    food_id: string;
    weight_g: number;
    consumed_ratio: number;
  }[];
  expected_revision?: number;
};

export type AgentConfidence = "high" | "medium" | "low";

export type AgentToolCallAudit = {
  name: string;
  effect: "read_only" | "proposal" | "confirmed_write";
  arguments: Record<string, unknown>;
  success: boolean;
  result_summary: string | null;
  error_code: string | null;
  cached: boolean;
};

export type AgentContext = {
  recorded_meals: number;
  expected_meals: number;
  remaining_energy_kcal: number;
  remaining_protein_g: number;
  remaining_fiber_g: number;
  gaps: string[];
  near_limits: string[];
  data_confidence: AgentConfidence;
  missing_data: string[];
  active_memories: string[];
};

export type AgentMemory = {
  id: string;
  category: "preference" | "avoidance" | "habit";
  value: string;
  status: "active" | "deleted";
  source_message: string;
  created_at: string;
  updated_at: string;
};

export type AIServiceStatus = {
  mode: "model" | "rules";
  provider: "deepseek" | "openai" | "rule";
  model: string | null;
  available: boolean;
  timeout_seconds: number;
  max_retries: number;
  configuration_issue: string | null;
  online_food_search_available: boolean;
  online_food_search_issue: string | null;
};

export type AgentFeedback = {
  trace_id: string;
  rating: "helpful" | "not_helpful";
};

export type AIChatResponse = {
  kind: "explanation" | "meal_record_proposal" | "exercise_record_proposal" | "consumption_advice" | "plan_recommendation" | "food_replacement" | "food_nutrition" | "dietary_knowledge" | "memory_proposal" | "safety_profile_proposal" | "clarification" | "safety" | "health_assessment";
  message: string;
  basis: string[];
  suggestions: string[];
  action: AIAction | null;
  cta: "preview_plans" | null;
  trace_id: string | null;
  confidence: AgentConfidence;
  decision_stage: "inform" | "clarify" | "propose" | "safety";
  context: AgentContext | null;
  needs_clarification: boolean;
  clarification_options: string[];
  tool_calls: AgentToolCallAudit[];
  provider: string;
  model: string | null;
  latency_ms: number | null;
  fallback_used: boolean;
  fallback_reason: string | null;
  fallback_retryable: boolean;
  intent_conflict: string | null;
};

export type AIHistoryTurn = {
  trace_id: string;
  user_message: string;
  assistant_response: AIChatResponse;
  created_at: string;
  feedback_rating: "helpful" | "not_helpful" | null;
};

export type AIHistoryPage = {
  items: AIHistoryTurn[];
  limit: number;
  has_more: boolean;
  next_cursor: string | null;
  next_before: string | null;
};
