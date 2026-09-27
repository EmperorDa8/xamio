// Sample faculty timetable for the landing-page demos. Dates are offsets from
// today so the countdowns never go stale. `tone` picks the pastel card colour.
export const TIMETABLE = [
  { code: "CSC 201", title: "Data Structures", day: 4, hour: 9, venue: "Hall B", tone: "pink" },
  { code: "MTH 211", title: "Linear Algebra", day: 5, hour: 13, venue: "LT 3", tone: "peach" },
  { code: "PHY 204", title: "Electromagnetism", day: 6, hour: 9, venue: "Room 12" , tone: "sky" },
  { code: "CSC 205", title: "Operating Systems", day: 7, hour: 9, venue: "Hall A", tone: "lavender" },
  { code: "ECO 101", title: "Microeconomics", day: 8, hour: 15, venue: "Main Aud.", tone: "mint" },
  { code: "CSC 209", title: "Database Systems", day: 9, hour: 13, venue: "Lab 2", tone: "mint" },
  { code: "GST 201", title: "Philosophy & Logic", day: 10, hour: 9, venue: "Hall C", tone: "peach" },
  { code: "STA 202", title: "Probability", day: 11, hour: 13, venue: "LT 1", tone: "sky" },
  { code: "CSC 211", title: "Computer Networks", day: 12, hour: 9, venue: "Room 404", tone: "lavender" },
  { code: "ENG 203", title: "Technical Writing", day: 13, hour: 15, venue: "Hall B", tone: "pink" },
];

// The hero scene's "registered courses" — the rows it lifts off the sheet.
export const HERO_PICKS = ["CSC 201", "CSC 205", "CSC 209", "CSC 211"];
