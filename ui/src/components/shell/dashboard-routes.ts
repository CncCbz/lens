export type DashboardView =
  | "overview"
  | "requests"
  | "channels"
  | "groups"
  | "multimodalRelay"
  | "settings"
  | "apiKeys"
  | "cronjobs"
  | "backups";

export type DashboardHref =
  | "/"
  | "/requests"
  | "/channels"
  | "/groups"
  | "/multimodal-relay"
  | "/settings"
  | "/api-keys"
  | "/cronjobs"
  | "/backups";

export const DASHBOARD_ROUTES: Record<DashboardView, DashboardHref> = {
  overview: "/",
  requests: "/requests",
  channels: "/channels",
  groups: "/groups",
  multimodalRelay: "/multimodal-relay",
  settings: "/settings",
  apiKeys: "/api-keys",
  cronjobs: "/cronjobs",
  backups: "/backups",
};

export function getDashboardViewFromPathname(pathname: string): DashboardView {
  for (const [view, href] of Object.entries(DASHBOARD_ROUTES) as [
    DashboardView,
    DashboardHref,
  ][]) {
    if (view === "overview") continue;
    if (pathname === href || pathname.startsWith(`${href}/`)) return view;
  }
  return "overview";
}
