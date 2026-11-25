"use strict";
const $ = (id) => document.getElementById(id),
  make = (tag, value) => {
    const el = document.createElement(tag);
    if (value !== undefined) el.textContent = String(value);
    return el;
  };
const list = (id, values) => {
  $(id).replaceChildren(...values.map((v) => make("li", v)));
};
const local = document.body.dataset.mode === "local";
const human = {
  load_population_dataset: "Standardise the population grid and age bands",
  reproject_dataset: "Project school locations to metric coordinates",
  filter_attributes: "Select the features matching the question",
  buffer_features: "Draw straight-line school service areas",
  nearest_features: "Measure each grid polygon to its nearest school point",
};
const stepLabel = (s, i) =>
  s.tool === "filter_attributes"
    ? i === 2
      ? "Select extracted school points"
      : "Select cells beyond the distance with enough children"
    : human[s.tool] || s.tool;
function sources(target, items) {
  target.replaceChildren(
    ...items.map((s) =>
      make("p", `${s.attribution} · ${s.license}. ${s.temporal_note}`),
    ),
  );
}
function render(result) {
  const d = result.report;
  $("result").hidden = false;
  $("preview-panel").hidden = true;
  const values = Object.fromEntries(
    d.metrics.map((m) => [m.metric_id, m.value]),
  );
  const fill = (t) =>
    t.replace(/\{([a-z][a-z0-9_]*)\}/g, (_, k) =>
      Number.isInteger(values[k])
        ? values[k].toLocaleString("en")
        : Number(values[k]).toPrecision(4),
    );
  $("question").textContent =
    `Which cells are more than ${d.parameters.distance_threshold_m.toLocaleString("en")} m from a school and contain at least ${d.parameters.children_threshold} children aged ${d.parameters.age_band_min}–${d.parameters.age_band_max}?`;
  $("headline").textContent =
    values.gap_cell_count === 0
      ? "No grid cells match these parameters. This is a valid empty result."
      : fill(d.headline_template);
  $("metrics").replaceChildren(
    ...[
      "gap_cell_count",
      "gap_children",
      "school_count",
      "grid_cell_count",
    ].map((id) => {
      const m = d.metrics.find((x) => x.metric_id === id),
        e = make("div");
      e.className = "metric";
      e.append(
        make("strong", m.value.toLocaleString("en")),
        make("span", m.label),
      );
      return e;
    }),
  );
  const svg = $("map"),
    ns = "http://www.w3.org/2000/svg";
  svg.replaceChildren();
  $("layers").replaceChildren();
  $("feature").textContent =
    "Select a feature on the map, or use the result table below.";
  let minx = Infinity,
    miny = Infinity,
    maxx = -Infinity,
    maxy = -Infinity;
  const walk = (c, f) => {
    if (typeof c[0] === "number") f(c);
    else c.forEach((x) => walk(x, f));
  };
  d.layers.forEach((l) =>
    l.geojson.features.forEach((ft) => {
      if (ft.geometry)
        walk(ft.geometry.coordinates, (p) => {
          minx = Math.min(minx, p[0]);
          miny = Math.min(miny, p[1]);
          maxx = Math.max(maxx, p[0]);
          maxy = Math.max(maxy, p[1]);
        });
    }),
  );
  const s = Math.min(
      860 / Math.max(maxx - minx, 1),
      490 / Math.max(maxy - miny, 1),
    ),
    ox = 20 + (860 - (maxx - minx) * s) / 2,
    oy = 20 + (490 - (maxy - miny) * s) / 2;
  const xy = (p) => [ox + (p[0] - minx) * s, 530 - oy - (p[1] - miny) * s];
  // Areas first, then cells, then schools, so individual school points stay selectable.
  const order = [d.layers[2], d.layers[0], d.layers[3], d.layers[1]];
  order.forEach((l) => {
    const g = document.createElementNS(ns, "g");
    svg.append(g);
    l.geojson.features.forEach((ft) => {
      const geom = ft.geometry;
      if (!geom) return;
      const shapes = [];
      const polys =
        geom.type === "Polygon"
          ? [geom.coordinates]
          : geom.type === "MultiPolygon"
            ? geom.coordinates
            : [];
      polys.forEach((poly) => {
        const e = document.createElementNS(ns, "path");
        e.setAttribute(
          "d",
          poly
            .map((r) => "M" + r.map((p) => xy(p).join(",")).join("L") + "Z")
            .join(""),
        );
        e.setAttribute("fill-rule", "evenodd");
        e.setAttribute("fill-opacity", l === d.layers[3] ? ".58" : ".18");
        e.setAttribute("stroke-width", l === d.layers[3] ? "1.5" : ".55");
        e.setAttribute("stroke", l.color);
        shapes.push(e);
      });
      if (geom.type === "Point") {
        const e = document.createElementNS(ns, "circle"),
          p = xy(geom.coordinates);
        e.setAttribute("cx", p[0]);
        e.setAttribute("cy", p[1]);
        e.setAttribute("r", 3);
        shapes.push(e);
      }
      shapes.forEach((e) => {
        e.setAttribute("fill", l.color);
        e.style.cursor = "pointer";
        const info =
          l.label +
          "\n" +
          l.popup_fields
            .map((k) => k + ": " + (ft.properties[k] ?? "unreported"))
            .join("\n");
        e.appendChild(document.createElementNS(ns, "title")).textContent = info;
        e.addEventListener("click", () => ($("feature").textContent = info));
        g.append(e);
      });
    });
    const label = make("label"),
      cb = make("input");
    cb.type = "checkbox";
    cb.checked = true;
    cb.addEventListener(
      "change",
      () => (g.style.display = cb.checked ? "" : "none"),
    );
    const sw = make("span");
    sw.className = "swatch";
    sw.style.background = l.color;
    label.append(
      cb,
      sw,
      make("span", `${l.label} (${l.preview_count}/${l.feature_count})`),
    );
    $("layers").append(label);
  });
  const head = make("tr");
  d.table.columns.forEach((c) =>
    head.append(make("th", c.replaceAll("_", " "))),
  );
  $("table").replaceChildren(
    head,
    ...d.table.rows.map((row) => {
      const tr = make("tr");
      row.forEach((v) => tr.append(make("td", v)));
      return tr;
    }),
  );
  $("table-note").textContent =
    `Showing ${d.table.rows.length} of ${d.table.total_rows} cells. Full result available below. GeoJSON coordinates are EPSG:3006; reproject for longitude/latitude applications.`;
  $("summary").replaceChildren(
    ...d.report_templates.map((t) => make("p", fill(t))),
  );
  $("downloads").replaceChildren(
    ...result.files
      .filter((f) =>
        ["coverage_gaps.csv", "coverage_gaps.geojson", "report.html"].includes(
          f.name,
        ),
      )
      .map((f) => {
        const a = make(
          "a",
          f.name === "report.html"
            ? "Open standalone report"
            : `Download ${f.name.endsWith("csv") ? "CSV" : "GeoJSON"}`,
        );
        a.href = f.name === "report.html" ? result.report_url || f.url : f.url;
        if (f.name !== "report.html") a.download = f.name;
        return a;
      }),
  );
  const html = result.files.find((f) => f.name === "report.html");
  if (html) {
    const a = make("a", "Download HTML");
    a.href = html.url;
    a.download = "report.html";
    $("downloads").append(a);
  }
  list(
    "steps",
    d.steps.map(
      (s, i) =>
        `${stepLabel(s, i)} — completed (${s.attempts} attempt${s.attempts === 1 ? "" : "s"}).`,
    ),
  );
  $("checks").textContent = result.checks.join(" · ");
  sources($("sources"), d.data_licenses);
  $("sources").append(
    ...d.sources.map((s) =>
      make(
        "p",
        `${s.role}: ${s.source} @ ${s.source_version} · ${s.row_count} rows · ${s.crs} · SHA-256 ${s.content_hash}`,
      ),
    ),
  );
  list("limits", [...d.assumptions, ...d.limitations]);
  list(
    "warnings",
    d.warnings.map((w) => "Warning: " + w),
  );
  $("run-meta").textContent =
    `${local ? "Current run" : "Saved run"} ${result.run_id} · ${result.created_at} · Code ${result.code_version}`;
  $("evidence-link").href = result.record_url;
}
async function post(action, body) {
  const r = await fetch("api/analyses/" + action, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok || !["plan", "success"].includes(data.status)) {
    const c = data.clarification;
    throw Error(
      c
        ? c.question
        : `${data.reason_code || data.status}: ${data.message || "This request is outside the supported school analysis."}`,
    );
  }
  return data;
}
if (local) {
  $("parameters").hidden = false;
  $("mode-label").textContent = "New analysis · local Python execution";
  $("mode-note").textContent =
    "Included Lund snapshots only. Preview the plan before running a new calculation.";
  const form = $("parameters");
  let previewed = false;
  const payload = () => {
    const f = new FormData(form);
    return {
      scenario: "school_coverage",
      place: f.get("place"),
      distance_m: Number(f.get("distance_m")),
      age_band: f.get("age_band"),
      minimum_children: Number(f.get("minimum_children")),
    };
  };
  const refreshQuestion = () => {
    const p = payload();
    $("question").textContent =
      `Which cells are more than ${p.distance_m} m from a school and contain at least ${p.minimum_children} children aged ${p.age_band}?`;
  };
  window.addEventListener("pageshow", () =>
    requestAnimationFrame(refreshQuestion),
  );
  form.addEventListener("input", () => {
    previewed = false;
    $("run").disabled = true;
    $("preview-panel").hidden = true;
    $("result").hidden = true;
    $("status").textContent = "Parameters changed. Preview a new plan.";
    const p = payload();
    $("question").textContent =
      `Which cells are more than ${p.distance_m} m from a school and contain at least ${p.minimum_children} children aged ${p.age_band}?`;
  });
  async function act(action) {
    refreshQuestion();
    const requestBody = payload();
    $("preview").disabled = true;
    $("run").disabled = true;
    $("result").hidden = true;
    $("status").textContent =
      action === "preview"
        ? "Checking request and data; no GIS execution."
        : "Running GIS tools and checking this result…";
    Array.from(form.elements).forEach((e) => (e.disabled = true));
    try {
      const data = await post(action, requestBody);
      if (action === "preview") {
        previewed = true;
        $("preview-panel").hidden = false;
        list(
          "preview-steps",
          data.steps.map((s, i) => stepLabel(s, i)),
        );
        sources($("preview-sources"), data.data_sources);
        $("status").textContent =
          "Plan ready. Review the steps and data, then run the analysis.";
      } else {
        render(data);
        $("status").textContent =
          "Analysis completed. Result and report checks passed.";
      }
    } catch (e) {
      previewed = false;
      $("status").textContent = e.message;
    } finally {
      Array.from(form.elements).forEach((e) => (e.disabled = false));
      $("run").disabled = !previewed;
    }
  }
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    act("preview");
  });
  $("run").addEventListener("click", () => {
    if (previewed) act("run");
  });
} else {
  fetch("examples/school/result.json")
    .then((r) => {
      if (!r.ok)
        throw Error(
          "Saved analysis could not be loaded. Serve the built website over HTTP.",
        );
      return r.json();
    })
    .then(render)
    .catch((e) => ($("status").textContent = e.message));
}
