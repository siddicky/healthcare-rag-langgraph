import { describe, expect, it } from "vitest";
import { sparseToWeekStrip } from "../weekstrip";

const THU_THIS_WEEK = "2026-08-20"; // Thursday, week Mon 2026-08-17 .. Sun 2026-08-23
const THU_NEXT_WEEK = "2026-08-27"; // Thursday, week Mon 2026-08-24 .. Sun 2026-08-30

describe("sparseToWeekStrip", () => {
  it("builds a Monday-first seven-slot strip with one real day and six muted", () => {
    const strip = sparseToWeekStrip([{ date: THU_THIS_WEEK, status: "logged" }]);
    expect(strip).toHaveLength(7);
    expect(strip.map((d) => d.label)).toEqual(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]);
    expect(strip[3]).toEqual({ label: "Thu", status: "logged" });
    expect(strip.filter((d) => d.status === "muted")).toHaveLength(6);
  });

  it("resolves a same-weekday collision to the anchor week (latest entry)", () => {
    const strip = sparseToWeekStrip([
      { date: THU_THIS_WEEK, status: "logged" },
      { date: THU_NEXT_WEEK, status: "upcoming" },
    ]);
    expect(strip[3]).toEqual({ label: "Thu", status: "upcoming" });
    expect(strip.some((d) => d.status === "logged")).toBe(false);
  });

  it("lets the last same-date duplicate win the shared slot", () => {
    const strip = sparseToWeekStrip([
      { date: THU_THIS_WEEK, status: "logged" },
      { date: THU_THIS_WEEK, status: "due" },
    ]);
    expect(strip[3]).toEqual({ label: "Thu", status: "due" });
  });

  it("honors an explicit anchor and drops entries outside that week", () => {
    const strip = sparseToWeekStrip([{ date: THU_NEXT_WEEK, status: "today" }], THU_THIS_WEEK);
    expect(strip[3]).toEqual({ label: "Thu", status: "muted" });
    expect(strip.every((d) => d.status === "muted")).toBe(true);
  });

  it("returns an all-muted strip for empty or invalid sparse input", () => {
    for (const sparse of [[], [{ date: "not-a-date", status: "logged" }], [{ date: THU_THIS_WEEK }]]) {
      const strip = sparseToWeekStrip(sparse);
      expect(strip).toHaveLength(7);
      expect(strip.every((d) => d.status === "muted")).toBe(true);
    }
  });
});
