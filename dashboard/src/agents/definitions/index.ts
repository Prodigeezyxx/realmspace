import { alertAgent } from "./alert";
import { dwellAgent } from "./dwell";
import { heatmapAgent } from "./heatmap";
import { layoutAgent } from "./layout";
import { nlqAgent } from "./nlq";
import { reportAgent } from "./report";
import { zoneAgent } from "./zone";

export const agentDefinitions = [
  dwellAgent,
  zoneAgent,
  heatmapAgent,
  reportAgent,
  alertAgent,
  nlqAgent,
  layoutAgent,
];
