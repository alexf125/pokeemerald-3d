const FULLSCREEN_VERTEX = /* wgsl */ `
struct VertexOutput {
  @builtin(position) position: vec4f,
  @location(0) uv: vec2f,
}

@vertex
fn vertexMain(@builtin(vertex_index) vertexIndex: u32) -> VertexOutput {
  var positions = array<vec2f, 3>(
    vec2f(-1.0, -1.0), vec2f(3.0, -1.0), vec2f(-1.0, 3.0),
  );
  var uvs = array<vec2f, 3>(
    vec2f(0.0, 1.0), vec2f(2.0, 1.0), vec2f(0.0, -1.0),
  );
  var output: VertexOutput;
  output.position = vec4f(positions[vertexIndex], 0.0, 1.0);
  output.uv = uvs[vertexIndex];
  return output;
}
`;

const PRESENT_SHADER = FULLSCREEN_VERTEX + /* wgsl */ `
@group(0) @binding(0) var sourceTexture: texture_2d<f32>;

@fragment
fn fragmentMain(input: VertexOutput) -> @location(0) vec4f {
  let size = vec2i(textureDimensions(sourceTexture));
  let uv = clamp(input.uv, vec2f(0.0), vec2f(0.99999));
  return textureLoad(sourceTexture, clamp(vec2i(uv * vec2f(size)), vec2i(0), size - 1), 0);
}
`;

const CINEMATIC_SHADER = FULLSCREEN_VERTEX + /* wgsl */ `
struct Camera {
  values: vec4f,
  viewport: vec4f,
  world: vec4f,
}
@group(0) @binding(0) var sceneTexture: texture_2d<f32>;
@group(0) @binding(1) var<uniform> camera: Camera;

fn loadScene(pixel: vec2i, size: vec2i) -> vec3f {
  return textureLoad(sceneTexture, clamp(pixel, vec2i(0), size - 1), 0).rgb;
}

fn luminance(color: vec3f) -> f32 {
  return dot(color, vec3f(0.2126, 0.7152, 0.0722));
}

fn highlight(color: vec3f) -> vec3f {
  let energy = smoothstep(0.57, 0.90, luminance(color));
  return color * energy;
}

fn screenHash(point: vec2f) -> f32 {
  return fract(sin(dot(point, vec2f(12.9898, 78.233))) * 43758.5453);
}

@fragment
fn fragmentMain(input: VertexOutput) -> @location(0) vec4f {
  let size = vec2i(textureDimensions(sceneTexture));
  let uv = clamp(input.uv, vec2f(0.0), vec2f(0.99999));
  let pixel = clamp(vec2i(uv * vec2f(size)), vec2i(0), size - 1);
  let base = loadScene(pixel, size);
  let renderScale = max(f32(size.x) / 240.0, 1.0);
  let centered = uv * 2.0 - vec2f(1.0);

  // HD-2D tilt-shift: keep the playable middle plane crisp while the far and
  // near edges fall gently out of focus. The radius scales with output size,
  // so the effect describes world detail rather than display pixels.
  let focusDistance = abs(uv.y - 0.53);
  let radialDistance = length(centered * vec2f(0.82, 1.0));
  let blurAmount = max(
    smoothstep(0.23, 0.52, focusDistance),
    0.45 * smoothstep(0.68, 1.12, radialDistance)
  );
  let radius = max(1, i32(ceil(renderScale * (0.75 + 1.35 * blurAmount))));
  let horizontal = vec2i(radius, 0);
  let vertical = vec2i(0, radius);
  let diagonal = vec2i(radius, radius);
  let ring = (
    loadScene(pixel - horizontal, size) + loadScene(pixel + horizontal, size)
    + loadScene(pixel - vertical, size) + loadScene(pixel + vertical, size)
    + loadScene(pixel - diagonal, size) + loadScene(pixel + diagonal, size)
    + loadScene(pixel + vec2i(radius, -radius), size)
    + loadScene(pixel + vec2i(-radius, radius), size)
  ) * 0.125;
  var color = mix(base, (2.0 * base + ring) / 3.0, 0.82 * blurAmount);

  // A compact bright-pass bloom gives water, pale roofs, signs, and windows a
  // soft high-resolution glow without softening dark outlines in the focus.
  let bloom = (
    highlight(loadScene(pixel - horizontal * 2, size))
    + highlight(loadScene(pixel + horizontal * 2, size))
    + highlight(loadScene(pixel - vertical * 2, size))
    + highlight(loadScene(pixel + vertical * 2, size))
    + highlight(loadScene(pixel - diagonal * 2, size))
    + highlight(loadScene(pixel + diagonal * 2, size))
    + highlight(loadScene(pixel + vec2i(radius * 2, -radius * 2), size))
    + highlight(loadScene(pixel + vec2i(-radius * 2, radius * 2), size))
  ) * 0.125;
  color += bloom * 0.23;

  // Filmic contrast and split toning tie the originally independent terrain
  // and upright sprites into one scene: cool ambient shadows, warm highlights.
  let originalLuma = luminance(color);
  color = mix(vec3f(originalLuma), color, 1.14);
  color = (color - vec3f(0.5)) * 1.105 + vec3f(0.5);
  let tonalPosition = smoothstep(0.12, 0.88, originalLuma);
  color *= mix(vec3f(0.975, 0.998, 1.035), vec3f(1.030, 1.010, 0.970), tonalPosition);

  // Soft optical falloff and a restrained upper-left key glow produce the
  // dramatic miniature-diorama framing without touching the flat UI pass.
  let vignetteShape = dot(centered * vec2f(0.82, 1.03), centered * vec2f(0.82, 1.03));
  let vignette = smoothstep(0.30, 1.08, vignetteShape);
  let keyGlow = pow(max(0.0, 1.0 - length((uv - vec2f(0.16, 0.08)) * vec2f(1.0, 1.25))), 3.0);
  color *= 1.0 - 0.31 * vignette;
  color += vec3f(1.0, 0.76, 0.48) * (0.045 * keyGlow);
  color += vec3f(screenHash(vec2f(pixel)) - 0.5) * 0.006;

  return vec4f(mix(base, clamp(color, vec3f(0.0), vec3f(1.0)), camera.values.z), 1.0);
}
`;

const TERRAIN_SHADER = /* wgsl */ `
struct Camera {
  values: vec4f,
  viewport: vec4f,
  world: vec4f,
}
struct VertexInput {
  @location(0) position: vec3f,
  @location(1) uv: vec2f,
  @location(2) shade: f32,
}
struct VertexOutput {
  @builtin(position) position: vec4f,
  @location(0) uv: vec2f,
  @location(1) shade: f32,
  @location(2) fog: f32,
}
@group(0) @binding(0) var worldTexture: texture_2d<f32>;
@group(0) @binding(1) var pixelSampler: sampler;
@group(0) @binding(2) var<uniform> camera: Camera;

@vertex
fn vertexMain(input: VertexInput) -> VertexOutput {
  let angle = radians(45.384615 * camera.values.x);
  let sine = sin(angle);
  let cosine = cos(angle);
  let cameraHeight = camera.values.w;
  let zoom = camera.values.y;
  let zoomOut = 0.30 * zoom + 0.76923077 * zoom * zoom * (zoom - 0.35);
  let focal = cameraHeight / (1.0 + zoomOut);
  let viewY = input.position.y * sine - input.position.z * cosine;
  let depth = cameraHeight - input.position.y * cosine - input.position.z * sine;
  let near = camera.viewport.z;
  let far = camera.viewport.w;
  var output: VertexOutput;
  output.position = vec4f(
    input.position.x * focal / (camera.viewport.x * 0.5),
    viewY * focal / (camera.viewport.y * 0.5),
    ((depth - near) / (far - near)) * depth,
    depth,
  );
  output.uv = input.uv;
  output.shade = input.shade;
  output.fog = clamp((depth - cameraHeight + 100.0) / 260.0, 0.0, 1.0);
  return output;
}

fn luminance(color: vec3f) -> f32 {
  return dot(color, vec3f(0.2126, 0.7152, 0.0722));
}

fn hash12(point: vec2f) -> f32 {
  var p = fract(vec3f(point.x, point.y, point.x) * 0.1031);
  p += dot(p, p.yzx + vec3f(33.33));
  return fract((p.x + p.y) * p.z);
}

fn valueNoise(point: vec2f) -> f32 {
  let cell = floor(point);
  let local = fract(point);
  let blend = local * local * (vec2f(3.0) - 2.0 * local);
  let north = mix(hash12(cell), hash12(cell + vec2f(1.0, 0.0)), blend.x);
  let south = mix(hash12(cell + vec2f(0.0, 1.0)), hash12(cell + vec2f(1.0)), blend.x);
  return mix(north, south, blend.y);
}

fn naturalNoise(point: vec2f) -> f32 {
  return valueNoise(point * 0.018)
    + 0.50 * valueNoise(point * 0.041 + vec2f(17.0, 29.0))
    + 0.25 * valueNoise(point * 0.089 + vec2f(47.0, 11.0));
}

fn loadWorld(pixel: vec2i, size: vec2i) -> vec3f {
  return textureLoad(worldTexture, clamp(pixel, vec2i(0), size - 1), 0).rgb;
}

@fragment
fn fragmentMain(input: VertexOutput) -> @location(0) vec4f {
  let size = vec2i(textureDimensions(worldTexture));
  let pixel = clamp(vec2i(input.uv * vec2f(size)), vec2i(0), size - 1);
  let world = camera.world.xy + vec2f(pixel) + vec2f(0.5);
  let base = textureSample(worldTexture, pixelSampler, input.uv).rgb;
  let baseLuma = luminance(base);

  // Read the authored pixel art as shallow relief. Dark pixels surrounded by
  // lighter terrain receive a tiny amount of ambient and directional shadow,
  // strengthening natural edges without blurring or inventing geometry.
  let neighborhoodLuma = 0.25 * (
    luminance(loadWorld(pixel + vec2i(-2, 0), size))
    + luminance(loadWorld(pixel + vec2i(2, 0), size))
    + luminance(loadWorld(pixel + vec2i(0, -2), size))
    + luminance(loadWorld(pixel + vec2i(0, 2), size))
  );
  let sunwardLuma = luminance(loadWorld(pixel + vec2i(-3, -4), size));
  let recess = max(neighborhoodLuma - baseLuma, 0.0);
  let leeEdge = max(sunwardLuma - baseLuma, 0.0);
  let localShadow = 1.0 - 0.115 * recess - 0.060 * leeEdge;

  // Low-frequency, world-anchored modulation resembles broad cloud/canopy
  // shadows. A much smaller texel-anchored term breaks up perfect gradients;
  // neither term moves between frames, so projected pixels never shimmer.
  let dapple = naturalNoise(world) / 1.75 - 0.5;
  let grain = hash12(world + vec2f(101.0, 37.0)) - 0.5;
  let atlasPosition = (vec2f(pixel) + vec2f(0.5)) / vec2f(size) - vec2f(0.5);
  let sunSweep = dot(atlasPosition, normalize(vec2f(-0.38, -0.92)));
  let naturalLight = 1.0 + 0.072 * dapple + 0.012 * grain + 0.038 * sunSweep;

  var lit = base * input.shade * localShadow * naturalLight;
  let warmth = clamp(0.52 + 2.4 * dapple + 0.45 * sunSweep, 0.0, 1.0);
  let coolShadow = vec3f(0.970, 0.990, 1.035);
  let warmSun = vec3f(1.035, 1.012, 0.968);
  lit *= mix(coolShadow, warmSun, warmth);

  let gray = vec3f(luminance(lit));
  let graded = mix(gray, lit, 1.12);
  let farHaze = vec3f(0.45, 0.68, 0.76);
  let atmospheric = mix(graded, farHaze, input.fog * 0.055);
  let edge = smoothstep(0.58, 1.16, length(atlasPosition * vec2f(1.0, 0.82)));
  let finished = atmospheric * (1.0 - 0.048 * edge);
  return vec4f(mix(base, finished, camera.values.z), 1.0);
}
`;

const BILLBOARD_SHADER = /* wgsl */ `
struct VertexInput {
  @location(0) position: vec2f,
  @location(1) uv: vec2f,
  @location(2) depth: f32,
  @location(3) layer: f32,
  @location(4) sourceSize: vec2f,
  @location(5) drawSize: vec2f,
  @location(6) affine: f32,
  @location(7) matrix: vec4f,
  @location(8) screenOrigin: vec2f,
  @location(9) objectInfo: vec2f,
}
struct VertexOutput {
  @builtin(position) position: vec4f,
  @location(0) uv: vec2f,
  @location(1) @interpolate(flat) layer: u32,
  @location(2) @interpolate(flat) sourceSize: vec2u,
  @location(3) @interpolate(flat) drawSize: vec2u,
  @location(4) @interpolate(flat) affine: u32,
  @location(5) @interpolate(flat) matrix: vec4i,
  @location(6) @interpolate(flat) screenOrigin: vec2i,
  @location(7) @interpolate(flat) objectInfo: vec2u,
}
@group(0) @binding(0) var objectTexture: texture_2d_array<f32>;
@group(0) @binding(1) var bgPriorityTexture: texture_2d<u32>;

@vertex
fn vertexMain(input: VertexInput) -> VertexOutput {
  var output: VertexOutput;
  output.position = vec4f(input.position.x / 120.0 - 1.0, 1.0 - input.position.y / 80.0, input.depth, 1.0);
  output.uv = input.uv;
  output.layer = u32(input.layer);
  output.sourceSize = vec2u(input.sourceSize);
  output.drawSize = vec2u(input.drawSize);
  output.affine = u32(input.affine);
  output.matrix = vec4i(input.matrix);
  output.screenOrigin = vec2i(input.screenOrigin);
  output.objectInfo = vec2u(input.objectInfo);
  return output;
}

struct FragmentOutput {
  @location(0) color: vec4f,
  @builtin(frag_depth) depth: f32,
}

@fragment
fn fragmentMain(input: VertexOutput) -> FragmentOutput {
  let dest = vec2i(floor(input.uv));
  let screen = input.screenOrigin + dest;
  let prioritySize = vec2i(textureDimensions(bgPriorityTexture));
  let atlas = screen + (prioritySize - vec2i(240, 160)) / 2;
  let inAtlas = all(atlas >= vec2i(0)) && all(atlas < prioritySize);
  var occluded = false;
  if (inAtlas) {
    let bgPriority = textureLoad(bgPriorityTexture, atlas, 0).r;
    if (bgPriority == 255u) { discard; }
    occluded = bgPriority < input.objectInfo.y;
  }

  var source = dest;
  if (input.affine != 0u) {
    let delta = dest - vec2i(input.drawSize) / 2;
    source = vec2i(
      (input.matrix.x * delta.x + input.matrix.y * delta.y) >> 8,
      (input.matrix.z * delta.x + input.matrix.w * delta.y) >> 8
    ) + vec2i(input.sourceSize) / 2;
  }
  if (any(source < vec2i(0)) || any(source >= vec2i(input.sourceSize))) { discard; }
  let color = textureLoad(objectTexture, source, i32(input.layer), 0);
  if (color.a <= 0.0) { discard; }
  var output: FragmentOutput;
  if (occluded) {
    // Preserve a readable whole-entity silhouette through overhead terrain
    // instead of slicing the billboard into disconnected visible fragments.
    output.color = vec4f(mix(color.rgb, vec3f(0.72, 0.88, 0.92), 0.24), color.a * 0.62);
  } else {
    output.color = color;
  }
  output.depth = select(input.position.z, 0.0, inAtlas);
  return output;
}
`;
const CAST_SHADOW_SHADER = /* wgsl */ `
struct Camera {
  values: vec4f,
  viewport: vec4f,
  world: vec4f,
}
struct VertexInput {
  @location(0) ground: vec3f,
  @location(1) uv: vec2f,
  @location(2) layer: f32,
  @location(3) sourceSize: vec2f,
  @location(4) drawSize: vec2f,
  @location(5) affine: f32,
  @location(6) matrix: vec4f,
  @location(7) priority: f32,
}
struct VertexOutput {
  @builtin(position) position: vec4f,
  @location(0) uv: vec2f,
  @location(1) @interpolate(flat) layer: u32,
  @location(2) @interpolate(flat) sourceSize: vec2u,
  @location(3) @interpolate(flat) drawSize: vec2u,
  @location(4) @interpolate(flat) affine: u32,
  @location(5) @interpolate(flat) matrix: vec4i,
  @location(6) ground: vec2f,
  @location(7) @interpolate(flat) priority: u32,
}
@group(0) @binding(0) var objectTexture: texture_2d_array<f32>;
@group(0) @binding(1) var<uniform> camera: Camera;
@group(0) @binding(2) var bgPriorityTexture: texture_2d<u32>;

@vertex
fn vertexMain(input: VertexInput) -> VertexOutput {
  let angle = radians(45.384615 * camera.values.x);
  let sine = sin(angle);
  let cosine = cos(angle);
  let cameraHeight = camera.values.w;
  let zoom = camera.values.y;
  let zoomOut = 0.30 * zoom + 0.76923077 * zoom * zoom * (zoom - 0.35);
  let focal = cameraHeight / (1.0 + zoomOut);
  let viewY = input.ground.y * sine - input.ground.z * cosine;
  let depth = cameraHeight - input.ground.y * cosine - input.ground.z * sine;
  let near = camera.viewport.z;
  let far = camera.viewport.w;
  var output: VertexOutput;
  output.position = vec4f(
    input.ground.x * focal / (camera.viewport.x * 0.5),
    viewY * focal / (camera.viewport.y * 0.5),
    (((depth - near) / (far - near)) - 0.00002) * depth,
    depth,
  );
  output.uv = input.uv;
  output.layer = u32(input.layer);
  output.sourceSize = vec2u(input.sourceSize);
  output.drawSize = vec2u(input.drawSize);
  output.affine = u32(input.affine);
  output.matrix = vec4i(input.matrix);
  output.ground = vec2f(input.ground.x, input.ground.z) + camera.viewport.xy * 0.5;
  output.priority = u32(input.priority);
  return output;
}

fn sourceCoordinate(dest: vec2i, input: VertexOutput) -> vec2i {
  if (input.affine == 0u) { return dest; }
  let delta = dest - vec2i(input.drawSize) / 2;
  return vec2i(
    (input.matrix.x * delta.x + input.matrix.y * delta.y) >> 8,
    (input.matrix.z * delta.x + input.matrix.w * delta.y) >> 8
  ) + vec2i(input.sourceSize) / 2;
}

fn objectAlpha(dest: vec2i, input: VertexOutput) -> f32 {
  // Match the billboard's exact visible destination mask before applying the
  // affine matrix. The padded shadow geometry can then soften the outline
  // without revealing source pixels that the real object does not draw.
  if (any(dest < vec2i(0)) || any(dest >= vec2i(input.drawSize))) { return 0.0; }
  let source = sourceCoordinate(dest, input);
  if (any(source < vec2i(0)) || any(source >= vec2i(input.sourceSize))) { return 0.0; }
  return textureLoad(objectTexture, source, i32(input.layer), 0).a;
}

@fragment
fn fragmentMain(input: VertexOutput) -> @location(0) vec4f {
  let prioritySize = vec2i(textureDimensions(bgPriorityTexture));
  let atlasOffset = (prioritySize - vec2i(camera.viewport.xy)) / 2;
  let atlasPixel = vec2i(floor(input.ground)) + atlasOffset;
  if (any(atlasPixel < vec2i(0)) || any(atlasPixel >= prioritySize)) { discard; }
  let receiverPriority = textureLoad(bgPriorityTexture, atlasPixel, 0).r;
  if (receiverPriority == 0xffu || receiverPriority < input.priority) { discard; }

  let dest = vec2i(floor(input.uv));
  let center = objectAlpha(dest, input);
  let cardinal = objectAlpha(dest + vec2i(-1, 0), input)
    + objectAlpha(dest + vec2i(1, 0), input)
    + objectAlpha(dest + vec2i(0, -1), input)
    + objectAlpha(dest + vec2i(0, 1), input);
  let diagonal = objectAlpha(dest + vec2i(-1, -1), input)
    + objectAlpha(dest + vec2i(1, -1), input)
    + objectAlpha(dest + vec2i(-1, 1), input)
    + objectAlpha(dest + vec2i(1, 1), input);
  let farCardinal = objectAlpha(dest + vec2i(-2, 0), input)
    + objectAlpha(dest + vec2i(2, 0), input)
    + objectAlpha(dest + vec2i(0, -2), input)
    + objectAlpha(dest + vec2i(0, 2), input);
  let farDiagonal = objectAlpha(dest + vec2i(-2, -2), input)
    + objectAlpha(dest + vec2i(2, -2), input)
    + objectAlpha(dest + vec2i(-2, 2), input)
    + objectAlpha(dest + vec2i(2, 2), input);
  let silhouette = clamp(center * 0.52 + cardinal * 0.055 + diagonal * 0.020
    + farCardinal * 0.025 + farDiagonal * 0.010, 0.0, 1.0);
  if (silhouette <= 0.005) { discard; }
  return vec4f(1.0, 1.0, 1.0, silhouette);
}
`;

const CAST_SHADOW_COMPOSITE_SHADER = FULLSCREEN_VERTEX + /* wgsl */ `
struct Camera {
  values: vec4f,
  viewport: vec4f,
  world: vec4f,
}
@group(0) @binding(0) var shadowMask: texture_2d<f32>;
@group(0) @binding(1) var<uniform> camera: Camera;

@fragment
fn fragmentMain(input: VertexOutput) -> @location(0) vec4f {
  let size = vec2i(textureDimensions(shadowMask));
  let pixel = clamp(vec2i(input.uv * vec2f(size)), vec2i(0), size - 1);
  let silhouette = textureLoad(shadowMask, pixel, 0).a;
  if (silhouette <= 0.005) { discard; }
  return vec4f(0.020, 0.052, 0.065, silhouette * 0.520 * camera.values.z);
}
`;

const UI_SHADER = FULLSCREEN_VERTEX + /* wgsl */ `
@group(0) @binding(0) var uiTexture: texture_2d<f32>;
@fragment
fn fragmentMain(input: VertexOutput) -> @location(0) vec4f {
  let size = vec2i(textureDimensions(uiTexture));
  let uv = clamp(input.uv, vec2f(0.0), vec2f(0.99999));
  let color = textureLoad(uiTexture, clamp(vec2i(uv * vec2f(size)), vec2i(0), size - 1), 0);
  if (color.a < 0.5) { discard; }
  return vec4f(color.rgb, 1.0);
}
`;

const TILE_SIZE = 8;
const CAMERA_HEIGHT = 480;
const CAMERA_TILT_DEGREES = 45.384615;
const CAMERA_NEAR = 32;
const CAMERA_FAR = 1024;
const VERTEX_FLOATS = 6;
const BILLBOARD_VERTEX_FLOATS = 19;
const CAST_SHADOW_VERTEX_FLOATS = 16;

function webGpuUnavailableError() {
  if (globalThis.isSecureContext === false) {
    return new Error(
      'WebGPU requires a secure HTTPS connection; plain HTTP IP addresses are not supported. With Tailscale, run “tailscale serve --bg 8000” on the host and open the HTTPS URL it prints. localhost remains available for local development.',
    );
  }
  return new Error(
    'This browser or device does not provide WebGPU. Update the operating system and use a WebGPU-capable browser.',
  );
}

class WebGpuPresenter {
  static async create(canvas, width, height, worldWidth, worldHeight, scale) {
    if (!navigator.gpu) throw webGpuUnavailableError();
    const adapter = await navigator.gpu.requestAdapter();
    if (!adapter) throw new Error('WebGPU is available, but no compatible graphics adapter was found.');
    const device = await adapter.requestDevice();
    return new WebGpuPresenter(canvas, width, height, worldWidth, worldHeight, scale, adapter, device);
  }

  constructor(canvas, width, height, worldWidth, worldHeight, scale, adapter, device) {
    Object.assign(this, { canvas, width, height, worldWidth, worldHeight, adapter, device });
    this.context = canvas.getContext('webgpu');
    if (!this.context) throw new Error('WebGPU canvas context is unavailable');
    this.format = navigator.gpu.getPreferredCanvasFormat();
    this.finalTexture = this.createTexture('canonical frame', 'rgba8unorm', GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.COPY_DST);
    this.worldTexture = this.createTexture('overscan world atlas', 'rgba8unorm', GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.COPY_DST, worldWidth, worldHeight);
    this.objectTexture = device.createTexture({
      label: 'independent OAM source layers', size: { width: 64, height: 64, depthOrArrayLayers: 128 },
      format: 'rgba8unorm', usage: GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.COPY_DST,
    });
    this.bgPriorityTexture = this.createTexture('world BG priority', 'r8uint', GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.COPY_DST, worldWidth, worldHeight);
    this.uiTexture = this.createTexture('flat BG0 interface', 'rgba8unorm', GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.COPY_DST);
    this.sceneWidth = width * scale;
    this.sceneHeight = height * scale;
    this.sceneTexture = this.createTexture('high-resolution 3D scene', 'rgba8unorm', GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.COPY_SRC, this.sceneWidth, this.sceneHeight);
    this.castShadowTexture = this.createTexture('unified projected shadow mask', 'rgba8unorm', GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.RENDER_ATTACHMENT, this.sceneWidth, this.sceneHeight);
    this.gradedTexture = this.createTexture('cinematic graded scene', 'rgba8unorm', GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.COPY_SRC, this.sceneWidth, this.sceneHeight);
    this.depthTexture = this.createTexture('high-resolution 3D depth', 'depth24plus', GPUTextureUsage.RENDER_ATTACHMENT, this.sceneWidth, this.sceneHeight);
    this.cameraBuffer = device.createBuffer({ label: '3D camera and lighting', size: 48, usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST });
    this.vertexCapacity = 1024 * 1024;
    this.vertexBuffer = device.createBuffer({ label: 'projected terrain mesh', size: this.vertexCapacity, usage: GPUBufferUsage.VERTEX | GPUBufferUsage.COPY_DST });
    this.billboardVertexCapacity = 64 * 1024;
    this.billboardVertexBuffer = device.createBuffer({ label: 'high-resolution billboard quads', size: this.billboardVertexCapacity, usage: GPUBufferUsage.VERTEX | GPUBufferUsage.COPY_DST });
    this.castShadowVertexCapacity = 64 * 1024;
    this.castShadowVertexBuffer = device.createBuffer({ label: 'projected sprite shadow geometry', size: this.castShadowVertexCapacity, usage: GPUBufferUsage.VERTEX | GPUBufferUsage.COPY_DST });
    this.uiPixels = new Uint8Array(width * height * 4);
    this.objectPixels = Array.from({ length: 128 }, () => []);
    this.tileHeights = new Float32Array((Math.ceil(worldWidth / TILE_SIZE) + 2) * (Math.ceil(worldHeight / TILE_SIZE) + 2));
    this.terrainCols = 0;
    this.terrainRows = 0;
    this.terrainOriginX = 0;
    this.terrainOriginY = 0;
    this.sampler = device.createSampler({ magFilter: 'nearest', minFilter: 'nearest', mipmapFilter: 'nearest' });
    this.createPipelines();
    this.resize(scale);
  }

  createTexture(label, format, usage, width = this.width, height = this.height) {
    return this.device.createTexture({ label, size: [width, height], format, usage });
  }

  createPipelines() {
    const { device } = this;
    const terrainModule = device.createShaderModule({ label: 'projected terrain shader', code: TERRAIN_SHADER });
    this.terrainPipeline = device.createRenderPipeline({
      label: 'depth-tested projected terrain', layout: 'auto',
      vertex: {
        module: terrainModule, entryPoint: 'vertexMain',
        buffers: [{ arrayStride: VERTEX_FLOATS * 4, attributes: [
          { shaderLocation: 0, offset: 0, format: 'float32x3' },
          { shaderLocation: 1, offset: 12, format: 'float32x2' },
          { shaderLocation: 2, offset: 20, format: 'float32' },
        ] }],
      },
      fragment: { module: terrainModule, entryPoint: 'fragmentMain', targets: [{ format: 'rgba8unorm' }] },
      primitive: { topology: 'triangle-list', cullMode: 'none' },
      depthStencil: { format: 'depth24plus', depthWriteEnabled: true, depthCompare: 'less' },
    });
    this.terrainBindGroup = device.createBindGroup({
      layout: this.terrainPipeline.getBindGroupLayout(0), entries: [
        { binding: 0, resource: this.worldTexture.createView() },
        { binding: 1, resource: this.sampler },
        { binding: 2, resource: { buffer: this.cameraBuffer } },
      ],
    });

    const castShadowModule = device.createShaderModule({ label: 'projected sprite shadow shader', code: CAST_SHADOW_SHADER });
    this.castShadowPipeline = device.createRenderPipeline({
      label: 'receiver-masked projected sprite shadows', layout: 'auto',
      vertex: {
        module: castShadowModule, entryPoint: 'vertexMain',
        buffers: [{ arrayStride: CAST_SHADOW_VERTEX_FLOATS * 4, attributes: [
          { shaderLocation: 0, offset: 0, format: 'float32x3' },
          { shaderLocation: 1, offset: 12, format: 'float32x2' },
          { shaderLocation: 2, offset: 20, format: 'float32' },
          { shaderLocation: 3, offset: 24, format: 'float32x2' },
          { shaderLocation: 4, offset: 32, format: 'float32x2' },
          { shaderLocation: 5, offset: 40, format: 'float32' },
          { shaderLocation: 6, offset: 44, format: 'float32x4' },
          { shaderLocation: 7, offset: 60, format: 'float32' },
        ] }],
      },
      fragment: { module: castShadowModule, entryPoint: 'fragmentMain', targets: [{
        format: 'rgba8unorm',
        blend: {
          color: { srcFactor: 'one', dstFactor: 'one', operation: 'max' },
          alpha: { srcFactor: 'one', dstFactor: 'one', operation: 'max' },
        },
      }] },
      primitive: { topology: 'triangle-list' },
    });
    this.castShadowBindGroup = device.createBindGroup({
      layout: this.castShadowPipeline.getBindGroupLayout(0), entries: [
        { binding: 0, resource: this.objectTexture.createView({ dimension: '2d-array' }) },
        { binding: 1, resource: { buffer: this.cameraBuffer } },
        { binding: 2, resource: this.bgPriorityTexture.createView() },
      ],
    });

    const castShadowCompositeModule = device.createShaderModule({ label: 'projected shadow composite shader', code: CAST_SHADOW_COMPOSITE_SHADER });
    this.castShadowCompositePipeline = device.createRenderPipeline({
      label: 'unified projected shadow composite', layout: 'auto',
      vertex: { module: castShadowCompositeModule, entryPoint: 'vertexMain' },
      fragment: { module: castShadowCompositeModule, entryPoint: 'fragmentMain', targets: [{
        format: 'rgba8unorm',
        blend: {
          color: { srcFactor: 'src-alpha', dstFactor: 'one-minus-src-alpha', operation: 'add' },
          alpha: { srcFactor: 'one', dstFactor: 'one-minus-src-alpha', operation: 'add' },
        },
      }] },
      primitive: { topology: 'triangle-list' },
    });

    const billboardModule = device.createShaderModule({ label: 'entity billboard shader', code: BILLBOARD_SHADER });
    this.billboardPipeline = device.createRenderPipeline({
      label: 'depth-tested upright entity billboards', layout: 'auto',
      vertex: {
        module: billboardModule, entryPoint: 'vertexMain',
        buffers: [{ arrayStride: BILLBOARD_VERTEX_FLOATS * 4, attributes: [
          { shaderLocation: 0, offset: 0, format: 'float32x2' },
          { shaderLocation: 1, offset: 8, format: 'float32x2' },
          { shaderLocation: 2, offset: 16, format: 'float32' },
          { shaderLocation: 3, offset: 20, format: 'float32' },
          { shaderLocation: 4, offset: 24, format: 'float32x2' },
          { shaderLocation: 5, offset: 32, format: 'float32x2' },
          { shaderLocation: 6, offset: 40, format: 'float32' },
          { shaderLocation: 7, offset: 44, format: 'float32x4' },
          { shaderLocation: 8, offset: 60, format: 'float32x2' },
          { shaderLocation: 9, offset: 68, format: 'float32x2' },
        ] }],
      },
      fragment: { module: billboardModule, entryPoint: 'fragmentMain', targets: [{
        format: 'rgba8unorm',
        blend: {
          color: { srcFactor: 'src-alpha', dstFactor: 'one-minus-src-alpha', operation: 'add' },
          alpha: { srcFactor: 'one', dstFactor: 'one-minus-src-alpha', operation: 'add' },
        },
      }] },
      primitive: { topology: 'triangle-list' },
      depthStencil: { format: 'depth24plus', depthWriteEnabled: true, depthCompare: 'less-equal' },
    });
    this.billboardBindGroup = device.createBindGroup({
      layout: this.billboardPipeline.getBindGroupLayout(0), entries: [
        { binding: 0, resource: this.objectTexture.createView({ dimension: '2d-array' }) },
        { binding: 1, resource: this.bgPriorityTexture.createView() },
      ],
    });

    const cinematicModule = device.createShaderModule({ label: 'cinematic HD-2D shader', code: CINEMATIC_SHADER });
    this.cinematicPipeline = device.createRenderPipeline({
      label: 'cinematic bloom, focus, and vignette', layout: 'auto',
      vertex: { module: cinematicModule, entryPoint: 'vertexMain' },
      fragment: { module: cinematicModule, entryPoint: 'fragmentMain', targets: [{ format: 'rgba8unorm' }] },
      primitive: { topology: 'triangle-list' },
    });

    const uiModule = device.createShaderModule({ label: 'flat interface shader', code: UI_SHADER });
    this.uiPipeline = device.createRenderPipeline({
      label: 'flat BG0 interface overlay', layout: 'auto',
      vertex: { module: uiModule, entryPoint: 'vertexMain' },
      fragment: { module: uiModule, entryPoint: 'fragmentMain', targets: [{ format: 'rgba8unorm' }] },
      primitive: { topology: 'triangle-list' },
    });
    this.uiBindGroup = device.createBindGroup({ layout: this.uiPipeline.getBindGroupLayout(0), entries: [{ binding: 0, resource: this.uiTexture.createView() }] });

    const presentModule = device.createShaderModule({ label: 'nearest presentation shader', code: PRESENT_SHADER });
    this.presentPipeline = device.createRenderPipeline({
      label: 'nearest presentation pipeline', layout: 'auto',
      vertex: { module: presentModule, entryPoint: 'vertexMain' },
      fragment: { module: presentModule, entryPoint: 'fragmentMain', targets: [{ format: this.format }] },
      primitive: { topology: 'triangle-list' },
    });
    const layout = this.presentPipeline.getBindGroupLayout(0);
    this.presentLayout = layout;
    this.finalBindGroup = device.createBindGroup({ layout, entries: [{ binding: 0, resource: this.finalTexture.createView() }] });
    this.rebuildSceneBindGroups();
  }

  rebuildSceneBindGroups() {
    this.castShadowCompositeBindGroup = this.device.createBindGroup({
      layout: this.castShadowCompositePipeline.getBindGroupLayout(0), entries: [
        { binding: 0, resource: this.castShadowTexture.createView() },
        { binding: 1, resource: { buffer: this.cameraBuffer } },
      ],
    });
    this.cinematicBindGroup = this.device.createBindGroup({
      layout: this.cinematicPipeline.getBindGroupLayout(0), entries: [
        { binding: 0, resource: this.sceneTexture.createView() },
        { binding: 1, resource: { buffer: this.cameraBuffer } },
      ],
    });
    this.scenePresentBindGroup = this.device.createBindGroup({
      layout: this.presentLayout, entries: [{ binding: 0, resource: this.gradedTexture.createView() }],
    });
  }

  get kind() { return 'webgpu-3d'; }

  resize(scale) {
    this.scale = scale;
    const width = this.width * scale;
    const height = this.height * scale;
    this.canvas.width = width;
    this.canvas.height = height;
    if (this.sceneWidth !== width || this.sceneHeight !== height) {
      this.sceneTexture.destroy();
      this.castShadowTexture.destroy();
      this.gradedTexture.destroy();
      this.depthTexture.destroy();
      this.sceneWidth = width;
      this.sceneHeight = height;
      this.sceneTexture = this.createTexture('high-resolution 3D scene', 'rgba8unorm', GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.COPY_SRC, width, height);
      this.castShadowTexture = this.createTexture('unified projected shadow mask', 'rgba8unorm', GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.RENDER_ATTACHMENT, width, height);
      this.gradedTexture = this.createTexture('cinematic graded scene', 'rgba8unorm', GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.COPY_SRC, width, height);
      this.depthTexture = this.createTexture('high-resolution 3D depth', 'depth24plus', GPUTextureUsage.RENDER_ATTACHMENT, width, height);
      this.rebuildSceneBindGroups();
    }
    this.context.configure({ device: this.device, format: this.format, alphaMode: 'opaque', usage: GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.COPY_SRC });
  }

  tileHeight(worldHeights, pixelX, pixelY) {
    let height = 0;
    for (let y = Math.max(0, pixelY); y < Math.min(this.worldHeight, pixelY + TILE_SIZE); y++) {
      const row = y * this.worldWidth;
      for (let x = Math.max(0, pixelX); x < Math.min(this.worldWidth, pixelX + TILE_SIZE); x++)
        height = Math.max(height, worldHeights[row + x]);
    }
    return height;
  }

  buildTerrain(worldHeights, gridOffsetX, gridOffsetY) {
    const originX = gridOffsetX - TILE_SIZE;
    const originY = gridOffsetY - TILE_SIZE;
    const cols = Math.ceil((this.worldWidth - originX) / TILE_SIZE);
    const rows = Math.ceil((this.worldHeight - originY) / TILE_SIZE);
    this.terrainCols = cols;
    this.terrainRows = rows;
    this.terrainOriginX = originX;
    this.terrainOriginY = originY;
    const heights = this.tileHeights;
    for (let y = 0; y < rows; y++) for (let x = 0; x < cols; x++) heights[y * cols + x] = this.tileHeight(worldHeights, originX + x * TILE_SIZE, originY + y * TILE_SIZE);
    const vertices = [];
    const pushVertex = (x, y, z, u, v, shade) => vertices.push(x, y, z, u, v, shade);
    const quad = (a, b, c, d, shade) => {
      for (const p of [a, b, c, a, c, d]) pushVertex(p[0], p[1], p[2], p[3], p[4], shade);
    };
    const halfW = this.worldWidth / 2;
    const halfH = this.worldHeight / 2;
    const heightAt = (x, y) => x < 0 || y < 0 || x >= cols || y >= rows ? 0 : heights[y * cols + x];
    for (let ty = 0; ty < rows; ty++) {
      for (let tx = 0; tx < cols; tx++) {
        const h = heightAt(tx, ty);
        const pixelX0 = originX + tx * TILE_SIZE;
        const pixelY0 = originY + ty * TILE_SIZE;
        const x0 = pixelX0 - halfW;
        const x1 = x0 + TILE_SIZE;
        const z0 = pixelY0 - halfH;
        const z1 = z0 + TILE_SIZE;
        const u0 = Math.max(0, pixelX0) / this.worldWidth;
        const u1 = Math.min(this.worldWidth, pixelX0 + TILE_SIZE) / this.worldWidth;
        const v0 = Math.max(0, pixelY0) / this.worldHeight;
        const v1 = Math.min(this.worldHeight, pixelY0 + TILE_SIZE) / this.worldHeight;
        const uWest = (Math.max(0, pixelX0) + 0.5) / this.worldWidth;
        const uEast = (Math.min(this.worldWidth, pixelX0 + TILE_SIZE) - 0.5) / this.worldWidth;
        const vNorth = (Math.max(0, pixelY0) + 0.5) / this.worldHeight;
        const vSouth = (Math.min(this.worldHeight, pixelY0 + TILE_SIZE) - 0.5) / this.worldHeight;
        quad([x0,h,z0,u0,v0],[x1,h,z0,u1,v0],[x1,h,z1,u1,v1],[x0,h,z1,u0,v1],1.0);
        const north = heightAt(tx, ty - 1);
        const south = heightAt(tx, ty + 1);
        const west = heightAt(tx - 1, ty);
        const east = heightAt(tx + 1, ty);
        if (north < h) quad([x1,north,z0,u1,vNorth],[x0,north,z0,u0,vNorth],[x0,h,z0,u0,vNorth],[x1,h,z0,u1,vNorth],0.68);
        if (south < h) quad([x0,south,z1,u0,vSouth],[x1,south,z1,u1,vSouth],[x1,h,z1,u1,vSouth],[x0,h,z1,u0,vSouth],0.88);
        if (west < h) quad([x0,west,z0,uWest,v0],[x0,west,z1,uWest,v1],[x0,h,z1,uWest,v1],[x0,h,z0,uWest,v0],0.64);
        if (east < h) quad([x1,east,z1,uEast,v1],[x1,east,z0,uEast,v0],[x1,h,z0,uEast,v0],[x1,h,z1,uEast,v1],0.82);
      }
    }
    const data = new Float32Array(vertices);
    if (data.byteLength > this.vertexCapacity) {
      this.vertexBuffer.destroy();
      this.vertexCapacity = 2 ** Math.ceil(Math.log2(data.byteLength));
      this.vertexBuffer = this.device.createBuffer({ label: 'projected terrain mesh', size: this.vertexCapacity, usage: GPUBufferUsage.VERTEX | GPUBufferUsage.COPY_DST });
    }
    this.device.queue.writeBuffer(this.vertexBuffer, 0, data);
    return data.length / VERTEX_FLOATS;
  }

  terrainHeightAt(x, z) {
    const atlasX = x + this.worldWidth / 2;
    const atlasZ = z + this.worldHeight / 2;
    const tileX = Math.floor((atlasX - this.terrainOriginX) / TILE_SIZE);
    const tileZ = Math.floor((atlasZ - this.terrainOriginY) / TILE_SIZE);
    if (tileX < 0 || tileZ < 0 || tileX >= this.terrainCols || tileZ >= this.terrainRows) return 0;
    return this.tileHeights[tileZ * this.terrainCols + tileX];
  }

  cameraProjection(x, y, z, tilt, zoom) {
    const angle = CAMERA_TILT_DEGREES * tilt * Math.PI / 180;
    const sine = Math.sin(angle);
    const cosine = Math.cos(angle);
    const zoomOut = 0.30 * zoom + 0.76923077 * zoom * zoom * (zoom - 0.35);
    const focal = CAMERA_HEIGHT / (1 + zoomOut);
    const viewY = y * sine - z * cosine;
    const depth = CAMERA_HEIGHT - y * cosine - z * sine;
    return {
      x: this.width / 2 + x * focal / depth,
      y: this.height / 2 - viewY * focal / depth,
      scale: focal / depth,
      depth: (depth - CAMERA_NEAR) / (CAMERA_FAR - CAMERA_NEAR),
    };
  }


  measureObjectFootRows(objectDescriptors, objectSourceCount, objectEventSpriteFlags,
                        objectSourcePixels) {
    const footRows = new Float32Array(objectEventSpriteFlags.length);
    footRows.fill(-32768);
    for (let layer = 0; layer < objectSourceCount; layer++) {
      const o = layer * 16;
      const spriteId = objectDescriptors[o + 10];
      if (spriteId < 0 || spriteId >= footRows.length || !objectEventSpriteFlags[spriteId]) continue;
      const screenY = objectDescriptors[o + 2];
      const sourceW = objectDescriptors[o + 5], sourceH = objectDescriptors[o + 6];
      const drawW = objectDescriptors[o + 7], drawH = objectDescriptors[o + 8];
      const affine = objectDescriptors[o + 11];
      const pa = objectDescriptors[o + 12], pb = objectDescriptors[o + 13];
      const pc = objectDescriptors[o + 14], pd = objectDescriptors[o + 15];
      for (let destY = drawH - 1; destY >= 0; destY--) {
        let rowVisible = false;
        for (let destX = 0; destX < drawW; destX++) {
          let sourceX = destX, sourceY = destY;
          if (affine) {
            const deltaX = destX - Math.floor(drawW / 2);
            const deltaY = destY - Math.floor(drawH / 2);
            sourceX = ((pa * deltaX + pb * deltaY) >> 8) + Math.floor(sourceW / 2);
            sourceY = ((pc * deltaX + pd * deltaY) >> 8) + Math.floor(sourceH / 2);
          }
          if (sourceX < 0 || sourceY < 0 || sourceX >= sourceW || sourceY >= sourceH) continue;
          const alpha = objectSourcePixels[((layer * 64 * 64 + sourceY * 64 + sourceX) * 4) + 3];
          if (alpha) { rowVisible = true; break; }
        }
        if (rowVisible) {
          footRows[spriteId] = Math.max(footRows[spriteId], screenY + destY + 1);
          break;
        }
      }
    }
    return footRows;
  }

  buildProjectedShadows(objectDescriptors, objectSourceCount, objectEventSpriteFlags,
                        objectFootRows) {
    const groups = new Map();
    for (let layer = 0; layer < objectSourceCount; layer++) {
      const o = layer * 16;
      const spriteId = objectDescriptors[o + 10];
      if (spriteId < 0 || spriteId >= objectEventSpriteFlags.length || !objectEventSpriteFlags[spriteId]) continue;
      const footY = objectFootRows[spriteId];
      if (footY <= -32768) continue;
      const descriptorAnchorY = objectDescriptors[o + 4];
      let group = groups.get(spriteId);
      if (!group) {
        group = { anchorX: objectDescriptors[o + 3], anchorY: footY, descriptorAnchorY,
          priority: objectDescriptors[o + 9] };
        groups.set(spriteId, group);
      } else {
        if (descriptorAnchorY > group.descriptorAnchorY) {
          group.anchorX = objectDescriptors[o + 3];
          group.descriptorAnchorY = descriptorAnchorY;
        }
        group.priority = Math.min(group.priority, objectDescriptors[o + 9]);
      }
    }

    const vertices = [];
    const vertex = (ground, u, v, layer, sourceW, sourceH, drawW, drawH, affine, pa, pb, pc, pd, priority) =>
      vertices.push(ground.x, ground.y, ground.z, u, v, layer, sourceW, sourceH, drawW, drawH,
                    affine, pa, pb, pc, pd, priority);
    for (let layer = 0; layer < objectSourceCount; layer++) {
      const o = layer * 16;
      const spriteId = objectDescriptors[o + 10];
      const group = groups.get(spriteId);
      if (!group) continue;
      const screenX = objectDescriptors[o + 1], screenY = objectDescriptors[o + 2];
      const sourceW = objectDescriptors[o + 5], sourceH = objectDescriptors[o + 6];
      const drawW = objectDescriptors[o + 7], drawH = objectDescriptors[o + 8];
      const affine = objectDescriptors[o + 11];
      const pa = objectDescriptors[o + 12], pb = objectDescriptors[o + 13];
      const pc = objectDescriptors[o + 14], pd = objectDescriptors[o + 15];
      const project = (u, v) => {
        // Sink the lowest opaque sprite row into the receiver plane so the
        // silhouette begins under the entity instead of after a raster gap.
        const height = Math.max(0, group.anchorY - (screenY + v) - 1);
        const x = group.anchorX - this.width / 2 + (screenX - group.anchorX + u) - height * 0.16;
        const z = group.anchorY - this.height / 2 - height * 1.65;
        return { x, y: this.terrainHeightAt(x, z), z };
      };
      // Leave a three-source-pixel apron around every projected component so
      // the wide alpha penumbra can cross tile and subsprite boundaries instead
      // of being clipped to each OAM rectangle.
      const u0 = -3, v0 = -3, u1 = drawW + 3;
      // Stop the component that owns the feet exactly at the shared foot row.
      // Extending its padded quad past that row would make interpolation lift
      // the last opaque sprite pixels away from the cast-shadow base.
      const footV = Math.max(0, group.anchorY - screenY);
      const v1 = Math.min(drawH + 3, footV);
      if (v1 <= v0) continue;
      const p00 = project(u0, v0), p10 = project(u1, v0);
      const p11 = project(u1, v1), p01 = project(u0, v1);
      for (const point of [
        [p00,u0,v0], [p10,u1,v0], [p11,u1,v1],
        [p00,u0,v0], [p11,u1,v1], [p01,u0,v1],
      ]) vertex(point[0], point[1], point[2], layer, sourceW, sourceH, drawW, drawH, affine, pa, pb, pc, pd, group.priority);
    }

    const data = new Float32Array(vertices);
    if (data.byteLength > this.castShadowVertexCapacity) {
      this.castShadowVertexBuffer.destroy();
      this.castShadowVertexCapacity = 2 ** Math.ceil(Math.log2(data.byteLength));
      this.castShadowVertexBuffer = this.device.createBuffer({ label: 'projected sprite shadow geometry', size: this.castShadowVertexCapacity, usage: GPUBufferUsage.VERTEX | GPUBufferUsage.COPY_DST });
    }
    if (data.byteLength) this.device.queue.writeBuffer(this.castShadowVertexBuffer, 0, data);
    return data.length / CAST_SHADOW_VERTEX_FLOATS;
  }

  buildFrameLayers(finalPixels, layerPixels, objectIds, objectPixels, objectPriorities,
                   objectDescriptors, objectSourceCount, tilt, zoom) {
    this.uiPixels.fill(0);
    for (const pixels of this.objectPixels) pixels.length = 0;
    for (let index = 0; index < layerPixels.length; index++) {
      if ((layerPixels[index] & 0x01) !== 0) {
        const o = index * 4;
        this.uiPixels[o] = finalPixels[o];
        this.uiPixels[o + 1] = finalPixels[o + 1];
        this.uiPixels[o + 2] = finalPixels[o + 2];
        this.uiPixels[o + 3] = 255;
      }
      const id = objectIds[index];
      if (id < 128) this.objectPixels[id].push(index);
    }

    const hasUiPlane = layerPixels.some((mask) => (mask & 0x01) !== 0);
    const objectTouchesUi = (x, y, width, height) => {
      const x0 = Math.max(0, x), y0 = Math.max(0, y);
      const x1 = Math.min(this.width, x + width), y1 = Math.min(this.height, y + height);
      for (let py = y0; py < y1; py++) for (let px = x0; px < x1; px++)
        if (layerPixels[py * this.width + px] & 0x01) return true;
      return false;
    };
    if (hasUiPlane) {
      for (let id = 0; id < 128; id++) {
        if (objectPriorities[id] !== 0) continue;
        for (const source of this.objectPixels[id]) {
          const src = source * 4;
          this.uiPixels[src] = objectPixels[src];
          this.uiPixels[src + 1] = objectPixels[src + 1];
          this.uiPixels[src + 2] = objectPixels[src + 2];
          this.uiPixels[src + 3] = objectPixels[src + 3];
        }
      }
    }

    // The engine exports the owning Sprite slot for each OAM entry. All
    // subsprites from one engine sprite share a foot-row projection without
    // accidentally joining touching actors or weather tiles.
    const groupAnchorY = new Int32Array(64);
    groupAnchorY.fill(-32768);
    for (let index = 0; index < objectSourceCount; index++) {
      const o = index * 16;
      const spriteId = objectDescriptors[o + 10];
      if (spriteId < groupAnchorY.length)
        groupAnchorY[spriteId] = Math.max(groupAnchorY[spriteId], objectDescriptors[o + 4]);
    }

    const order = Array.from({ length: objectSourceCount }, (_, index) => index);
    order.sort((a, b) => {
      const ao = a * 16, bo = b * 16;
      return objectDescriptors[bo + 9] - objectDescriptors[ao + 9]
        || objectDescriptors[bo] - objectDescriptors[ao];
    });
    const vertices = [];
    const vertex = (x, y, u, v, depth, layer, sourceW, sourceH, drawW, drawH,
                    affine, pa, pb, pc, pd, screenX, screenY, oamId, priority) =>
      vertices.push(x, y, u, v, depth, layer, sourceW, sourceH, drawW, drawH,
                    affine, pa, pb, pc, pd, screenX, screenY, oamId, priority);
    for (const layer of order) {
      const o = layer * 16;
      const oamId = objectDescriptors[o];
      const screenX = objectDescriptors[o + 1];
      const screenY = objectDescriptors[o + 2];
      const anchorX = objectDescriptors[o + 3];
      const spriteId = objectDescriptors[o + 10];
      const anchorY = spriteId < groupAnchorY.length ? groupAnchorY[spriteId] : objectDescriptors[o + 4];
      const sourceW = objectDescriptors[o + 5];
      const sourceH = objectDescriptors[o + 6];
      const drawW = objectDescriptors[o + 7];
      const drawH = objectDescriptors[o + 8];
      const priority = objectDescriptors[o + 9];
      const affine = objectDescriptors[o + 11];
      const pa = objectDescriptors[o + 12], pb = objectDescriptors[o + 13];
      const pc = objectDescriptors[o + 14], pd = objectDescriptors[o + 15];
      if (hasUiPlane && priority === 0 && objectTouchesUi(screenX, screenY, drawW, drawH)) continue;

      const projected = this.cameraProjection(anchorX - this.width / 2, 0, anchorY - this.height / 2, tilt, zoom);
      const x0 = projected.x + (screenX - anchorX) * projected.scale;
      const x1 = x0 + drawW * projected.scale;
      const y0 = projected.y + (screenY - anchorY) * projected.scale;
      const y1 = y0 + drawH * projected.scale;
      // Native BG priority supplies exact overhead masking. Keep the card in
      // front of unrelated terrain fragments so its middle cannot be sliced away.
      const depth = projected.depth;
      const extra = [depth, layer, sourceW, sourceH, drawW, drawH, affine, pa, pb, pc, pd,
                     screenX, screenY, oamId, priority];
      for (const point of [
        [x0,y0,0,0], [x1,y0,drawW,0], [x1,y1,drawW,drawH],
        [x0,y0,0,0], [x1,y1,drawW,drawH], [x0,y1,0,drawH],
      ]) vertex(point[0], point[1], point[2], point[3], ...extra);
    }

    const data = new Float32Array(vertices);
    if (data.byteLength > this.billboardVertexCapacity) {
      this.billboardVertexBuffer.destroy();
      this.billboardVertexCapacity = 2 ** Math.ceil(Math.log2(data.byteLength));
      this.billboardVertexBuffer = this.device.createBuffer({ label: 'high-resolution billboard quads', size: this.billboardVertexCapacity, usage: GPUBufferUsage.VERTEX | GPUBufferUsage.COPY_DST });
    }
    if (data.byteLength) this.device.queue.writeBuffer(this.billboardVertexBuffer, 0, data);
    return data.length / BILLBOARD_VERTEX_FLOATS;
  }

  writeTexture(texture, pixels, width = this.width, height = this.height, bytesPerPixel = 4) {
    this.device.queue.writeTexture({ texture }, pixels, { bytesPerRow: width * bytesPerPixel, rowsPerImage: height }, { width, height });
  }

  writeObjectSources(pixels, count) {
    if (!count) return;
    const bytes = count * 64 * 64 * 4;
    this.device.queue.writeTexture(
      { texture: this.objectTexture }, pixels.subarray(0, bytes),
      { bytesPerRow: 64 * 4, rowsPerImage: 64 },
      { width: 64, height: 64, depthOrArrayLayers: count },
    );
  }

  present({ finalPixels, worldPixels, worldHeightPixels, worldGridOffsetX, worldGridOffsetY, worldPixelOriginX, worldPixelOriginY, layerPixels, objectIds, bgPriorities, objectSourcePixels, objectDescriptors, objectEventSpriteFlags, objectSourceCount, objectPixels, objectPriorities, enhanced, shading, perspective, zoom }) {
    const encoder = this.device.createCommandEncoder({ label: 'pokeemerald frame encoder' });
    let presentBindGroup = this.finalBindGroup;
    if (enhanced) {
      this.writeTexture(this.worldTexture, worldPixels, this.worldWidth, this.worldHeight);
      const vertexCount = this.buildTerrain(worldHeightPixels, worldGridOffsetX, worldGridOffsetY);
      const billboardVertexCount = this.buildFrameLayers(finalPixels, layerPixels, objectIds, objectPixels, objectPriorities, objectDescriptors, objectSourceCount, perspective, zoom);
      const objectFootRows = this.measureObjectFootRows(objectDescriptors, objectSourceCount, objectEventSpriteFlags, objectSourcePixels);
      const castShadowVertexCount = this.buildProjectedShadows(objectDescriptors, objectSourceCount, objectEventSpriteFlags, objectFootRows);
      this.writeObjectSources(objectSourcePixels, objectSourceCount);
      this.writeTexture(this.bgPriorityTexture, bgPriorities, this.worldWidth, this.worldHeight, 1);
      this.writeTexture(this.uiTexture, this.uiPixels);
      this.device.queue.writeBuffer(this.cameraBuffer, 0, new Float32Array([perspective, zoom, shading, CAMERA_HEIGHT, this.width, this.height, CAMERA_NEAR, CAMERA_FAR, worldPixelOriginX, worldPixelOriginY, 0, 0]));

      const terrainPass = encoder.beginRenderPass({
        label: 'depth-tested projected terrain pass',
        colorAttachments: [{ view: this.sceneTexture.createView(), clearValue: { r: 0.08, g: 0.15, b: 0.18, a: 1 }, loadOp: 'clear', storeOp: 'store' }],
        depthStencilAttachment: { view: this.depthTexture.createView(), depthClearValue: 1, depthLoadOp: 'clear', depthStoreOp: 'store' },
      });
      terrainPass.setPipeline(this.terrainPipeline); terrainPass.setBindGroup(0, this.terrainBindGroup); terrainPass.setVertexBuffer(0, this.vertexBuffer); terrainPass.draw(vertexCount); terrainPass.end();

      const castShadowPass = encoder.beginRenderPass({
        label: 'unified projected shadow mask pass',
        colorAttachments: [{ view: this.castShadowTexture.createView(), clearValue: { r: 0, g: 0, b: 0, a: 0 }, loadOp: 'clear', storeOp: 'store' }],
      });
      castShadowPass.setPipeline(this.castShadowPipeline); castShadowPass.setBindGroup(0, this.castShadowBindGroup); castShadowPass.setVertexBuffer(0, this.castShadowVertexBuffer); castShadowPass.draw(castShadowVertexCount); castShadowPass.end();

      const castShadowCompositePass = encoder.beginRenderPass({
        label: 'unified projected shadow composite pass',
        colorAttachments: [{ view: this.sceneTexture.createView(), loadOp: 'load', storeOp: 'store' }],
      });
      castShadowCompositePass.setPipeline(this.castShadowCompositePipeline); castShadowCompositePass.setBindGroup(0, this.castShadowCompositeBindGroup); castShadowCompositePass.draw(3); castShadowCompositePass.end();

      const billboardPass = encoder.beginRenderPass({
        label: 'upright billboard pass',
        colorAttachments: [{ view: this.sceneTexture.createView(), loadOp: 'load', storeOp: 'store' }],
        depthStencilAttachment: { view: this.depthTexture.createView(), depthLoadOp: 'load', depthStoreOp: 'store' },
      });
      billboardPass.setPipeline(this.billboardPipeline); billboardPass.setBindGroup(0, this.billboardBindGroup); billboardPass.setVertexBuffer(0, this.billboardVertexBuffer); billboardPass.draw(billboardVertexCount); billboardPass.end();

      const cinematicPass = encoder.beginRenderPass({
        label: 'cinematic HD-2D post-process',
        colorAttachments: [{ view: this.gradedTexture.createView(), clearValue: { r: 0, g: 0, b: 0, a: 1 }, loadOp: 'clear', storeOp: 'store' }],
      });
      cinematicPass.setPipeline(this.cinematicPipeline); cinematicPass.setBindGroup(0, this.cinematicBindGroup); cinematicPass.draw(3); cinematicPass.end();

      const uiPass = encoder.beginRenderPass({ label: 'flat interface pass', colorAttachments: [{ view: this.gradedTexture.createView(), loadOp: 'load', storeOp: 'store' }] });
      uiPass.setPipeline(this.uiPipeline); uiPass.setBindGroup(0, this.uiBindGroup); uiPass.draw(3); uiPass.end();
      presentBindGroup = this.scenePresentBindGroup;
    } else {
      this.writeTexture(this.finalTexture, finalPixels);
    }

    const pass = encoder.beginRenderPass({ colorAttachments: [{ view: this.context.getCurrentTexture().createView(), clearValue: { r: 0, g: 0, b: 0, a: 1 }, loadOp: 'clear', storeOp: 'store' }] });
    pass.setPipeline(this.presentPipeline); pass.setBindGroup(0, presentBindGroup); pass.draw(3); pass.end();
    this.device.queue.submit([encoder.finish()]);
  }

  async ready() { await this.device.queue.onSubmittedWorkDone(); }

  destroy() {
    for (const resource of [this.finalTexture,this.worldTexture,this.objectTexture,this.bgPriorityTexture,this.uiTexture,this.sceneTexture,this.castShadowTexture,this.gradedTexture,this.depthTexture,this.vertexBuffer,this.billboardVertexBuffer,this.castShadowVertexBuffer,this.cameraBuffer]) resource.destroy();
    this.device.destroy();
  }
}

export async function createPresenter({ canvas, width, height, worldWidth, worldHeight, scale }) {
  return WebGpuPresenter.create(canvas, width, height, worldWidth, worldHeight, scale);
}
