// stems-all.inc: rs-keylights' addition to rearmed-mods' Song Stems helper (rs_stems.m includes it, as
// tools/drums-setup.sh builds it): every part asked for from one pass of the model. Each segment's run of htdemucs_6s
// gives all six of its sources, and `separate` keeps one, so a song's five parts took five passes.
//
//   rs-stems-drums separate-all <wem> <dir> <part>...      parts: drums bass other vocals guitar piano
//
// Each part goes to <dir>/<part>.raw: the part itself, 16-bit and interleaved, as long as the song and at its rate and
// channels (as `decode` gives the song). <dir>/progress says the segments done ("k n") as it goes. However long the
// song, its memory stays small: each part's kept only where segments still overlap (a ring), written out once it's
// done at the model's rate (float, a file a channel), then resampled to the song's rate from there.

static const char *const ALL_NAME[] = {"drums", "bass", "other", "vocals", "guitar", "piano"};  // the model's order
#define ALL_RING ((size_t)2 * SEG)  // frames a part's kept for (segments overlap by less than one)
#define ALL_CHUNK ((size_t)65536)   // frames written out at a time

typedef struct {
  int source;
  stft *post;
  float *res[2], *wave[2], *ring[2];
  FILE *f[2];  // at the model's rate, as it's done
  char path[2][PATH_MAX];
} all_part;

static int all_source(const char *name) {
  for (int i = 0; i < 6; i++)
    if (!strcmp(name, ALL_NAME[i])) return i;
  return -1;
}

// Segment k's parts from the model's outputs (each on a thread of its own), into their rings, cross-faded as
// post_segment does
static void all_post(sep *c, all_part *parts, int nparts, float *wsum, int k, MLMultiArray *xo, MLMultiArray *xto) {
  NSArray<NSNumber *> *sx = xo.strides, *stt = xto.strides;
  size_t cs = sx[1].unsignedLongValue, rs = sx[2].unsignedLongValue;
  size_t tcs = stt[1].unsignedLongValue, trs = stt[2].unsignedLongValue;
  [xo getBytesWithHandler:^(const void *xb, NSInteger xsize) {
    [xto getBytesWithHandler:^(const void *tb, NSInteger tsize) {
      (void)xsize;
      (void)tsize;
      const float *xf = xb, *tf = tb;
      dispatch_apply((size_t)nparts, DISPATCH_APPLY_AUTO, ^(size_t n) {
        all_part *p = &parts[n];
        const float *spec[4], *wave[2];
        for (int i = 0; i < 4; i++) spec[i] = xf + (size_t)(4 * p->source + i) * cs;
        for (int i = 0; i < 2; i++) {
          const float *w = tf + (size_t)(2 * p->source + i) * tcs;
          if (trs != FOLD_W) {  // rows with padding: gather them
            for (int r = 0; r < FOLD_ROWS; r++) memcpy(p->wave[i] + r * FOLD_W, w + r * trs, FOLD_W * sizeof(float));
            w = p->wave[i];
          }
          wave[i] = w;
        }
        seg_post(p->post, spec, rs, wave, &c->st[k], p->res[0], p->res[1]);
      });
    }];
  }];
  size_t o = (size_t)k * c->stride, len = c->n44 - o < SEG ? c->n44 - o : SEG, d = (SEG - len) / 2;  // center_trim
  for (size_t i = 0; i < len; i++) {
    float w = seg_weight((int)i);
    size_t at = (o + i) % ALL_RING;
    for (int n = 0; n < nparts; n++) {
      parts[n].ring[0][at] += w * parts[n].res[0][d + i];
      parts[n].ring[1][at] += w * parts[n].res[1][d + i];
    }
    wsum[at] += w;
  }
}

// Frames [from, upto) of every part done (at the model's rate): written out, their places in the rings freed
static int all_finish(sep *c, all_part *parts, int nparts, float *wsum, size_t from, size_t upto, float *buf,
                      char *err, size_t errlen) {
  for (int n = 0; n < nparts; n++)
    for (int ch = 0; ch < 2; ch++) {
      for (size_t i = from; i < upto; i++) {
        size_t at = i % ALL_RING;
        buf[i - from] = parts[n].ring[ch][at] / wsum[at] * c->std + c->mean;
        parts[n].ring[ch][at] = 0;
      }
      if (fwrite(buf, sizeof(float), upto - from, parts[n].f[ch]) != upto - from)
        FAIL("can't write %s: %s", parts[n].path[ch], strerror(errno));
    }
  for (size_t i = from; i < upto; i++) wsum[i % ALL_RING] = 0;
  return 0;
}

// rs_run, writing out[0] on for j0 on
static void all_resample(const resampler *r, const float *in, size_t n, float *out, size_t j0, size_t j1) {
  int taps = 2 * r->half;
  for (size_t j = j0; j < j1; j++) {
    uint64_t pos = (uint64_t)j * (uint64_t)r->down;
    long start = (long)(pos / (uint64_t)r->up) - r->half + 1;
    const float *h = r->taps + (size_t)(pos % (uint64_t)r->up) * (size_t)taps;
    if (start >= 0 && (size_t)start + (size_t)taps <= n) {
      vDSP_dotpr(in + start, 1, h, 1, out + (j - j0), (vDSP_Length)taps);
    } else {
      float acc = 0;
      for (int i = 0; i < taps; i++)
        if (start + i >= 0 && (size_t)(start + i) < n) acc += in[start + i] * h[i];
      out[j - j0] = acc;
    }
  }
}

// A part at the song's rate and channels, 16-bit, from its files at the model's rate (then gone)
static int all_write(sep *c, all_part *p, const char *dir, char *err, size_t errlen) {
  const song *s = c->s;
  size_t bytes = c->n44 * sizeof(float);
  const float *in[2] = {NULL, NULL};
  for (int ch = 0; ch < 2; ch++) {
    int fd = open(p->path[ch], O_RDONLY);
    void *m = fd < 0 ? MAP_FAILED : mmap(NULL, bytes, PROT_READ, MAP_PRIVATE, fd, 0);
    if (fd >= 0) close(fd);
    if (m == MAP_FAILED) {
      if (in[0]) munmap((void *)in[0], bytes);
      FAIL("can't read %s: %s", p->path[ch], strerror(errno));
    }
    in[ch] = m;
  }
  char path[PATH_MAX];
  snprintf(path, sizeof path, "%s/%s.raw", dir, ALL_NAME[p->source]);
  FILE *f = fopen(path, "wb");
  float *part[2] = {malloc(ALL_CHUNK * sizeof(float)), malloc(ALL_CHUNK * sizeof(float))};
  int16_t *out = malloc(ALL_CHUNK * s->channels * sizeof(int16_t));
  int ok = f && part[0] && part[1] && out;
  for (size_t j0 = 0; ok && j0 < s->frames; j0 += ALL_CHUNK) {
    size_t j1 = j0 + ALL_CHUNK < s->frames ? j0 + ALL_CHUNK : s->frames;
    for (int ch = 0; ch < 2; ch++) {
      if (c->resample)
        all_resample(&c->up, in[ch], c->n44, part[ch], j0, j1);
      else
        memcpy(part[ch], in[ch] + j0, (j1 - j0) * sizeof(float));
    }
    for (size_t j = 0; j < j1 - j0; j++)
      for (uint32_t ch = 0; ch < s->channels; ch++) {
        float v = s->channels == 1 ? 0.5f * (part[0][j] + part[1][j]) : part[ch][j];
        v = rintf(v * 32768.f);
        out[j * s->channels + ch] = (int16_t)(v > 32767.f ? 32767.f : v < -32768.f ? -32768.f : v);
      }
    ok = fwrite(out, sizeof(int16_t), (j1 - j0) * s->channels, f) == (j1 - j0) * s->channels;
  }
  int closed = f ? fclose(f) : EOF;
  free(part[0]);
  free(part[1]);
  free(out);
  for (int ch = 0; ch < 2; ch++) {
    munmap((void *)in[ch], bytes);
    unlink(p->path[ch]);
  }
  if (!ok || closed) FAIL("can't write %s: %s", path, strerror(errno));
  return 0;
}

#define ALL_FAIL(...)                   \
  do {                                  \
    snprintf(err, errlen, __VA_ARGS__); \
    goto out;                           \
  } while (0)

static int all_run(const char *wem, const char *dir, all_part *parts, int nparts, char *err, size_t errlen) {
  song s = {0};
  sep c = {0};
  sep *cp = &c;
  double decode_s, started = now();
  float *wsum = calloc(ALL_RING, sizeof(float)), *buf = malloc(SEG * sizeof(float));
  int rc = -1;
  char progress_buf[PATH_MAX], werr_buf[512] = "";
  char *progress = progress_buf, *werr = werr_buf;  // (blocks take pointers, not arrays)
  snprintf(progress_buf, sizeof progress_buf, "%s/progress", dir);
  dispatch_queue_t q = dispatch_get_global_queue(QOS_CLASS_UTILITY, 0);
  MLMultiArray *prev_x = nil, *prev_xt = nil;
  __block size_t done44 = 0;
  __block int bad_write = 0;
  double separated = 0;
  if (load_song(wem, &s, &decode_s, err, errlen)) goto out;
  c.s = &s;
  if (sep_setup(&c, err, errlen)) goto out;
  if (!wsum || !buf) ALL_FAIL("out of memory");
  for (int n = 0; n < nparts; n++) {
    all_part *p = &parts[n];
    p->post = stft_new();
    for (int ch = 0; ch < 2; ch++) {
      p->res[ch] = malloc(SEG * sizeof(float));
      p->wave[ch] = malloc(FOLD * sizeof(float));
      p->ring[ch] = calloc(ALL_RING, sizeof(float));
      snprintf(p->path[ch], sizeof p->path[ch], "%s/%s.%d.f32", dir, ALL_NAME[p->source], ch);
      p->f[ch] = fopen(p->path[ch], "wb");
      if (!p->res[ch] || !p->wave[ch] || !p->ring[ch]) ALL_FAIL("out of memory");
      if (!p->f[ch]) ALL_FAIL("can't write %s: %s", p->path[ch], strerror(errno));
    }
    if (!p->post) ALL_FAIL("out of memory");
  }
  // As separate_song: the model on one segment while the CPU prepares the next and finishes the one before
  prep_segment(&c, 0);
  for (int k = 0; k <= c.nseg; k++) {
    dispatch_group_t g = dispatch_group_create();
    if (k + 1 < c.nseg) dispatch_group_async(g, q, ^{ prep_segment(cp, k + 1); });
    if (k > 0) {
      MLMultiArray *a = prev_x, *b = prev_xt;
      dispatch_group_async(g, q, ^{
        all_post(cp, parts, nparts, wsum, k - 1, a, b);
        size_t upto = k < cp->nseg ? (size_t)k * cp->stride : cp->n44;
        if (all_finish(cp, parts, nparts, wsum, done44, upto, buf, werr, 512)) bad_write = 1;
        done44 = upto;
        FILE *pf = fopen(progress, "w");
        if (pf) {
          fprintf(pf, "%d %d\n", k, cp->nseg);
          fclose(pf);
        }
      });
    }
    MLMultiArray *xo = nil, *xto = nil;
    int bad = k < c.nseg && model_predict(c.x[k & 1], c.xt[k & 1], &xo, &xto, err, errlen);
    dispatch_group_wait(g, DISPATCH_TIME_FOREVER);
    if (bad) goto out;
    if (bad_write) ALL_FAIL("%s", werr);
    prev_x = xo;
    prev_xt = xto;
  }
  separated = now() - started;
  for (int n = 0; n < nparts; n++)
    for (int ch = 0; ch < 2; ch++) {
      int closed = fclose(parts[n].f[ch]);
      parts[n].f[ch] = NULL;
      if (closed) ALL_FAIL("can't write %s: %s", parts[n].path[ch], strerror(errno));
    }
  for (int n = 0; n < nparts; n++)
    if (all_write(&c, &parts[n], dir, err, errlen)) goto out;
  printf("%u frames at %u Hz decoded in %.2f s; %d parts separated in %.2f s (%d segments), written in %.2f s\n",
         s.frames, s.rate, decode_s, nparts, separated, c.nseg, now() - started - separated);
  rc = 0;
out:
  for (int n = 0; n < nparts; n++) {
    all_part *p = &parts[n];
    for (int ch = 0; ch < 2; ch++) {
      if (p->f[ch]) fclose(p->f[ch]);
      if (rc && p->path[ch][0]) unlink(p->path[ch]);
      free(p->res[ch]);
      free(p->wave[ch]);
      free(p->ring[ch]);
    }
    if (p->post) stft_free(p->post);
  }
  free(wsum);
  free(buf);
  sep_free(&c);
  free(s.pcm);
  return rc;
}

static int separate_all(const char *wem, const char *dir, int nnames, char **names) {
  all_part parts[6];
  memset(parts, 0, sizeof parts);
  int nparts = 0;
  for (int i = 0; i < nnames; i++) {
    int source = all_source(names[i]);
    if (source < 0) {
      fprintf(stderr, "rs-stems: no part %s (the parts: drums bass other vocals guitar piano)\n", names[i]);
      return 2;
    }
    int again = 0;
    for (int n = 0; n < nparts; n++) again |= parts[n].source == source;
    if (!again) parts[nparts++].source = source;
  }
  char err[512];
  if (model_load(err, sizeof err)) {
    fprintf(stderr, "rs-stems: %s\n", err);
    return 1;
  }
  printf("model loaded in %.2f s; overlap %.2f\n", g_model_load_s, overlap());
  fflush(stdout);
  __block int rc = 0;
  all_part *pp = parts;
  dispatch_semaphore_t done = dispatch_semaphore_create(0);
  dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
    char e[512];
    rc = all_run(wem, dir, pp, nparts, e, sizeof e);
    if (rc) fprintf(stderr, "rs-stems: %s\n", e);
    dispatch_semaphore_signal(done);
  });
  dispatch_semaphore_wait(done, DISPATCH_TIME_FOREVER);
  if (rc) return 1;
  struct rusage ru;
  getrusage(RUSAGE_SELF, &ru);
  printf("peak memory %.0f MB\n", ru.ru_maxrss / 1048576.0);
  return 0;
}
