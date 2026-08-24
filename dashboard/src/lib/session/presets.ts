/**
 * Session type catalog + per-type defaults.
 *
 * The wizard pulls everything from here: the type cards on step 1, and the
 * preset zones + touchpoints on steps 3 and 4. Each preset is designed to
 * be a sensible starting point — the user can edit, delete, add as they go.
 */

import {
  Building,
  Camera,
  Frame,
  GraduationCap,
  Heart,
  type LucideIcon,
  Mic2,
  Plus,
  Rocket,
  ShoppingBag,
  Sparkles,
  Store,
} from "lucide-react";

import { BRAND_BLUE, BRAND_DATA } from "@/lib/brand";
import type {
  PrimaryObjective,
  SessionType,
  Touchpoint,
  TouchpointType,
  Zone,
  ZoneType,
} from "./types";

// ── Type catalog ──────────────────────────────────────────────────────────

export interface SessionTypeMeta {
  type: SessionType;
  label: string;
  short: string;          // one-line description for the card
  blurb: string;          // longer description on hover / detail
  icon: LucideIcon;
  /** Example sub-types shown as tag-list on the card. */
  examples: string[];
}

export const SESSION_TYPES: SessionTypeMeta[] = [
  {
    type: "brand_activation",
    label: "Brand activation",
    short: "Pop-up, sponsor lounge, festival tent.",
    blurb:
      "Short-run brand experiences built to attract, engage and measure — the classic agency-led activation.",
    icon: Sparkles,
    examples: ["Pop-up booth", "Festival tent", "Sponsor lounge", "Launch party"],
  },
  {
    type: "exhibition",
    label: "Exhibition / Gallery",
    short: "Museum, design week, art show.",
    blurb:
      "Curated showcases where dwell, attention and flow tell you what visitors actually engaged with.",
    icon: Frame,
    examples: ["Museum", "Design week", "Art show", "Showroom"],
  },
  {
    type: "conference",
    label: "Conference / Summit",
    short: "Multi-track event with stages and lounges.",
    blurb:
      "Industry summits and corporate events — measure room fill, sponsor exposure and networking density.",
    icon: Mic2,
    examples: ["Industry summit", "Internal corporate", "Tech conference"],
  },
  {
    type: "trade_show",
    label: "Trade show booth",
    short: "Exhibitor stand at a wider expo.",
    blurb:
      "Single-booth measurement for B2B floors — visitor capture, demo engagement, and lead quality signals.",
    icon: Building,
    examples: ["Industry expo", "B2B floor", "Convention stand"],
  },
  {
    type: "experience_centre",
    label: "Experience centre",
    short: "Flagship store, brand HQ, automotive XPC.",
    blurb:
      "Permanent or semi-permanent destination spaces — track product zones, consultations, and repeat visits.",
    icon: Store,
    examples: ["Flagship store", "Brand HQ", "Automotive XPC", "Concept store"],
  },
  {
    type: "retail_popup",
    label: "Retail pop-up",
    short: "Temporary store or holiday activation.",
    blurb:
      "Try-on, fitting and checkout flow with attention-to-conversion measurement across the in-store funnel.",
    icon: ShoppingBag,
    examples: ["Holiday store", "Seasonal activation", "Concept pop-up"],
  },
  {
    type: "product_launch",
    label: "Product launch",
    short: "VIP preview, press unveil, public reveal.",
    blurb:
      "High-stakes single moments. Capture press attention, demo dwell, and the social proof of hero shots.",
    icon: Rocket,
    examples: ["VIP preview", "Press unveil", "Public reveal", "Influencer night"],
  },
  {
    type: "workshop",
    label: "Workshop / Training",
    short: "Corporate learning, certifications, R&D lab.",
    blurb:
      "Classroom + lab spaces where engagement with materials and stations correlates with learning outcomes.",
    icon: GraduationCap,
    examples: ["Corporate L&D", "Certifications", "R&D lab"],
  },
  {
    type: "press_event",
    label: "Press / Premiere",
    short: "Red carpet, screening, gala.",
    blurb:
      "Press density, photo-wall throughput, and reception engagement — measurement built for the comms team.",
    icon: Camera,
    examples: ["Red carpet", "Premiere", "Gala", "Awards night"],
  },
  {
    type: "private_event",
    label: "Private event",
    short: "Wedding, member gala, investor day.",
    blurb:
      "Anonymous flow + zone analytics for private spaces where attribution matters but identity must not.",
    icon: Heart,
    examples: ["Wedding", "Member gala", "Investor day"],
  },
  {
    type: "custom",
    label: "Custom",
    short: "Define it yourself from a blank canvas.",
    blurb:
      "For anything that doesn't fit the patterns above — start blank and build the layout your way.",
    icon: Plus,
    examples: [],
  },
];

export function getTypeMeta(type: SessionType): SessionTypeMeta {
  return SESSION_TYPES.find((t) => t.type === type) ?? SESSION_TYPES[SESSION_TYPES.length - 1];
}

// ── Zone & touchpoint catalogues ──────────────────────────────────────────

/**
 * Default colour per zone type.
 *
 * **Deliberately wider than the brand palette, and it has to be.** `brand.md`
 * gives two accents and one supporting blue; there are eleven zone types here,
 * and their whole job on a heatmap or in the twin is to be told apart at a
 * glance. Three colours cannot do that, so the brand pair anchors the set — the
 * entry and demo zones, the ones an operator looks at first, wear the Data
 * colour — and the rest are chosen for separation.
 *
 * The one hard constraint the re-token added: **nothing here may sit near the
 * Action orange `#FF5C00`**, because that colour now means "something needs
 * attention" everywhere else in the product. Sponsor used to be `#ff8a4d`,
 * which after the re-token read as a permanent alert on the floor plan.
 */
export const ZONE_TYPE_OPTIONS: { value: ZoneType; label: string; color: string }[] = [
  { value: "entry",          label: "Entry",            color: BRAND_DATA },
  { value: "reveal",         label: "Hero / Reveal",    color: "#b66bff" },
  { value: "engagement",     label: "Engagement",       color: BRAND_BLUE },
  { value: "lounge",         label: "Lounge",           color: "#00d4ff" },
  { value: "retail",         label: "Retail",           color: "#ffc83d" },
  { value: "sponsor",        label: "Sponsor",          color: "#f472b6" },
  { value: "demo",           label: "Demo / Stage",     color: BRAND_DATA },
  { value: "press",          label: "Press",            color: "#ff4d4d" },
  { value: "exit",           label: "Exit",             color: "#ffd60a" },
  { value: "privacy_masked", label: "Privacy-masked",   color: "#6e7180" },
  { value: "other",          label: "Other",            color: "#a8aab5" },
];

export const TOUCHPOINT_TYPE_OPTIONS: { value: TouchpointType; label: string }[] = [
  { value: "screen",          label: "Screen / Display" },
  { value: "rfid",            label: "RFID Reader" },
  { value: "quiz",            label: "Quiz / Interactive" },
  { value: "game",            label: "Game" },
  { value: "photo_booth",     label: "Photo booth" },
  { value: "scent_station",   label: "Scent station" },
  { value: "product_display", label: "Product display" },
  { value: "ar_mirror",       label: "AR Mirror" },
  { value: "configurator",    label: "Configurator" },
  { value: "demo_unit",       label: "Demo unit" },
  { value: "voice",           label: "Voice / Assistant" },
  { value: "wayfinding",      label: "Wayfinding" },
  { value: "lead_form",       label: "Lead form" },
  { value: "badge_scan",      label: "Badge scan" },
  { value: "audio_guide",     label: "Audio guide" },
  { value: "other",           label: "Other" },
];

export const PRIMARY_OBJECTIVE_OPTIONS: { value: PrimaryObjective; label: string }[] = [
  { value: "brand_awareness",   label: "Brand awareness" },
  { value: "lead_capture",      label: "Lead capture" },
  { value: "product_education", label: "Product education" },
  { value: "vip_engagement",    label: "VIP engagement" },
  { value: "sales_conversion",  label: "Sales conversion" },
  { value: "press_coverage",    label: "Press coverage" },
  { value: "research",          label: "Research" },
  { value: "loyalty",           label: "Loyalty / retention" },
];

// ── Presets per session type ──────────────────────────────────────────────

type ZonePreset = Omit<Zone, "id">;
type TouchpointPreset = Omit<Touchpoint, "id" | "zoneId"> & {
  /** Match a preset zone by name — wizard resolves to id. */
  zoneName?: string;
};

interface TypeDefaults {
  zones: ZonePreset[];
  touchpoints: TouchpointPreset[];
}

const PRESETS: Record<SessionType, TypeDefaults> = {
  brand_activation: {
    zones: [
      { name: "Entry Arch",   type: "entry",       capacity: 10, color: BRAND_DATA },
      { name: "Hero Reveal",  type: "reveal",      capacity: 25, color: "#b66bff" },
      { name: "Engagement",   type: "engagement",  capacity: 20, color: BRAND_BLUE },
      { name: "Sponsor Wall", type: "sponsor",     capacity: 15, color: "#f472b6" },
      { name: "Lounge",       type: "lounge",      capacity: 18, color: "#00d4ff" },
      { name: "Exit",         type: "exit",        capacity: 10, color: "#ffd60a" },
    ],
    touchpoints: [
      { name: "AR Mirror",         type: "ar_mirror",   zoneName: "Engagement",  triggers: ["viewed", "interacted"] },
      { name: "Brand Quiz",        type: "quiz",        zoneName: "Engagement",  triggers: ["started", "completed"] },
      { name: "Photo Wall",        type: "photo_booth", zoneName: "Hero Reveal", triggers: ["captured"] },
      { name: "RFID Memory Wall",  type: "rfid",        zoneName: "Exit",        triggers: ["tapped"] },
      { name: "Sponsor Screen",    type: "screen",      zoneName: "Sponsor Wall",triggers: ["viewed"] },
    ],
  },
  exhibition: {
    zones: [
      { name: "Entry",         type: "entry",      capacity: 8,  color: BRAND_DATA },
      { name: "Curator Intro", type: "reveal",     capacity: 15, color: "#b66bff" },
      { name: "Gallery 1",     type: "engagement", capacity: 25, color: BRAND_BLUE },
      { name: "Gallery 2",     type: "engagement", capacity: 25, color: "#00d4ff" },
      { name: "Reading Nook",  type: "lounge",     capacity: 12, color: "#ffc83d" },
      { name: "Bookshop",      type: "retail",     capacity: 10, color: "#f472b6" },
    ],
    touchpoints: [
      { name: "Audio Guide Trigger", type: "audio_guide", zoneName: "Entry",       triggers: ["activated"] },
      { name: "Info Kiosk",          type: "screen",      zoneName: "Curator Intro", triggers: ["viewed"] },
      { name: "Interactive Exhibit", type: "quiz",        zoneName: "Gallery 1",   triggers: ["interacted"] },
      { name: "Wishlist Scanner",    type: "rfid",        zoneName: "Bookshop",    triggers: ["tapped"] },
    ],
  },
  conference: {
    zones: [
      { name: "Registration",  type: "entry",      capacity: 50,  color: BRAND_DATA },
      { name: "Main Stage",    type: "demo",       capacity: 500, color: "#b66bff" },
      { name: "Breakout A",    type: "engagement", capacity: 80,  color: BRAND_BLUE },
      { name: "Breakout B",    type: "engagement", capacity: 80,  color: "#00d4ff" },
      { name: "Networking",    type: "lounge",     capacity: 120, color: "#ffc83d" },
      { name: "Sponsor Hall",  type: "sponsor",    capacity: 100, color: "#f472b6" },
      { name: "Press Room",    type: "press",      capacity: 30,  color: "#ff4d4d" },
    ],
    touchpoints: [
      { name: "Badge Scan",        type: "badge_scan",  zoneName: "Registration", triggers: ["checked_in"] },
      { name: "Session Check-in",  type: "rfid",        zoneName: "Main Stage",   triggers: ["entered"] },
      { name: "Sponsor Demo",      type: "demo_unit",   zoneName: "Sponsor Hall", triggers: ["interacted"] },
      { name: "Lead Form",         type: "lead_form",   zoneName: "Sponsor Hall", triggers: ["submitted"] },
    ],
  },
  trade_show: {
    zones: [
      { name: "Stand Entry",   type: "entry",      capacity: 8,  color: BRAND_DATA },
      { name: "Demo Area",     type: "demo",       capacity: 12, color: "#b66bff" },
      { name: "Meeting Pods",  type: "lounge",     capacity: 6,  color: "#00d4ff" },
      { name: "Giveaways",     type: "retail",     capacity: 6,  color: "#ffd60a" },
    ],
    touchpoints: [
      { name: "Product Demo",  type: "demo_unit",  zoneName: "Demo Area",   triggers: ["interacted"] },
      { name: "Lead Form",     type: "lead_form",  zoneName: "Meeting Pods",triggers: ["submitted"] },
      { name: "Badge Scan",    type: "badge_scan", zoneName: "Stand Entry", triggers: ["checked_in"] },
    ],
  },
  experience_centre: {
    zones: [
      { name: "Welcome Lounge",  type: "entry",      capacity: 15, color: BRAND_DATA },
      { name: "Product Zone A",  type: "engagement", capacity: 20, color: BRAND_BLUE },
      { name: "Product Zone B",  type: "engagement", capacity: 20, color: "#00d4ff" },
      { name: "Demo Theatre",    type: "demo",       capacity: 40, color: "#b66bff" },
      { name: "Consultation",    type: "lounge",     capacity: 8,  color: "#ffc83d" },
      { name: "Café",            type: "lounge",     capacity: 25, color: "#f472b6" },
    ],
    touchpoints: [
      { name: "Configurator",      type: "configurator", zoneName: "Product Zone A", triggers: ["started", "saved"] },
      { name: "Demo Unit",         type: "demo_unit",    zoneName: "Demo Theatre",   triggers: ["interacted"] },
      { name: "Specialist Call",   type: "voice",        zoneName: "Consultation",   triggers: ["requested"] },
      { name: "Loyalty Scan",      type: "rfid",         zoneName: "Welcome Lounge", triggers: ["tapped"] },
    ],
  },
  retail_popup: {
    zones: [
      { name: "Window",   type: "entry",      capacity: 4,  color: BRAND_DATA },
      { name: "Entry",    type: "entry",      capacity: 10, color: BRAND_BLUE },
      { name: "Try-on",   type: "engagement", capacity: 15, color: "#b66bff" },
      { name: "Fitting",  type: "engagement", capacity: 6,  color: "#00d4ff" },
      { name: "Checkout", type: "retail",     capacity: 5,  color: "#ffc83d" },
      { name: "Lounge",   type: "lounge",     capacity: 10, color: "#f472b6" },
    ],
    touchpoints: [
      { name: "AR Mirror",     type: "ar_mirror", zoneName: "Try-on",   triggers: ["viewed", "tried"] },
      { name: "Style Quiz",    type: "quiz",      zoneName: "Lounge",   triggers: ["completed"] },
      { name: "RFID Hangtag",  type: "rfid",      zoneName: "Try-on",   triggers: ["tapped"] },
      { name: "Checkout POS",  type: "lead_form", zoneName: "Checkout", triggers: ["purchased"] },
    ],
  },
  product_launch: {
    zones: [
      { name: "Press Arrival",  type: "entry",      capacity: 30,  color: BRAND_DATA },
      { name: "Hero Reveal",    type: "reveal",     capacity: 80,  color: "#b66bff" },
      { name: "Demo Stations",  type: "demo",       capacity: 40,  color: BRAND_BLUE },
      { name: "VIP Lounge",     type: "lounge",     capacity: 25,  color: "#ffc83d" },
      { name: "Q&A Stage",      type: "engagement", capacity: 100, color: "#00d4ff" },
      { name: "Press Wall",     type: "press",      capacity: 15,  color: "#ff4d4d" },
    ],
    touchpoints: [
      { name: "Hero Unit",     type: "product_display", zoneName: "Hero Reveal",   triggers: ["viewed", "approached"] },
      { name: "Demo Unit",     type: "demo_unit",       zoneName: "Demo Stations", triggers: ["interacted"] },
      { name: "Photo Wall",    type: "photo_booth",     zoneName: "Press Wall",    triggers: ["captured"] },
      { name: "Sponsor Screen",type: "screen",          zoneName: "VIP Lounge",    triggers: ["viewed"] },
    ],
  },
  workshop: {
    zones: [
      { name: "Reception",  type: "entry",      capacity: 12, color: BRAND_DATA },
      { name: "Classroom",  type: "demo",       capacity: 25, color: "#b66bff" },
      { name: "Lab",        type: "engagement", capacity: 15, color: BRAND_BLUE },
      { name: "Breakout",   type: "lounge",     capacity: 10, color: "#00d4ff" },
    ],
    touchpoints: [
      { name: "Lab Station",    type: "demo_unit", zoneName: "Lab",       triggers: ["used"] },
      { name: "Material Kiosk", type: "screen",    zoneName: "Reception", triggers: ["viewed"] },
    ],
  },
  press_event: {
    zones: [
      { name: "Red Carpet",  type: "entry",      capacity: 20, color: BRAND_DATA },
      { name: "Photo Wall",  type: "press",      capacity: 15, color: "#ff4d4d" },
      { name: "Auditorium",  type: "demo",       capacity: 200,color: "#b66bff" },
      { name: "Reception",   type: "lounge",     capacity: 80, color: "#ffc83d" },
      { name: "Press Lounge",type: "press",      capacity: 30, color: "#f472b6" },
    ],
    touchpoints: [
      { name: "Press Desk",     type: "lead_form",   zoneName: "Reception",   triggers: ["registered"] },
      { name: "Photo Wall",     type: "photo_booth", zoneName: "Photo Wall",  triggers: ["captured"] },
      { name: "Sponsor Screen", type: "screen",      zoneName: "Press Lounge",triggers: ["viewed"] },
    ],
  },
  private_event: {
    zones: [
      { name: "Arrival",        type: "entry",      capacity: 12, color: BRAND_DATA },
      { name: "Reception",      type: "lounge",     capacity: 60, color: "#b66bff" },
      { name: "Main Room",      type: "demo",       capacity: 120,color: BRAND_BLUE },
      { name: "Private Dining", type: "lounge",     capacity: 40, color: "#ffc83d" },
    ],
    touchpoints: [
      { name: "Photo Booth",    type: "photo_booth", zoneName: "Reception", triggers: ["captured"] },
      { name: "RFID Guest Book",type: "rfid",        zoneName: "Arrival",   triggers: ["tapped"] },
    ],
  },
  custom: {
    zones: [],
    touchpoints: [],
  },
};

export function presetZonesFor(type: SessionType): ZonePreset[] {
  return PRESETS[type].zones;
}

export function presetTouchpointsFor(
  type: SessionType
): TouchpointPreset[] {
  return PRESETS[type].touchpoints;
}

// ── Status meta (chip colours) ────────────────────────────────────────────

export const STATUS_META: Record<
  import("./types").SessionStatus,
  { label: string; variant: "live" | "info" | "success" | "neutral" | "warn" }
> = {
  draft:     { label: "Draft",     variant: "neutral" },
  scheduled: { label: "Scheduled", variant: "info" },
  live:      { label: "Live",      variant: "live" },
  paused:    { label: "Paused",    variant: "warn" },
  completed: { label: "Completed", variant: "success" },
};
