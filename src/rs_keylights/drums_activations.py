"""drums_activations.py DRUMS.WAV OUT.npy: ADTOF's strengths for a drum track (100 a second; kick, snare, tom,
hi-hat, cymbal), saved as float16. Runs in the drum transcriber's own Python (tools/drums-setup.sh), not
rs-keylights': rs_keylights.drums starts it.

ADTOF's own transcribe_to_midi runs its convolutions over the whole song at once: for 5 minutes, 6 GB at its peak
(their working space), which on an 8 GB Mac pushes the game and rs-keylights out to swap while it runs. Here they run
CHUNK frames at a time, each with MARGIN frames of the song either side (more than they see), and only the recurrent
layers over the whole song: the same strengths (checked on a song: not a bit different), at 1.2 GB for 5 minutes,
most of it the spectrogram ADTOF's audio code makes first, and faster."""
import sys

import numpy as np
import torch
from adtof_pytorch import get_default_weights_path, load_audio_for_model, transcribe_to_midi  # pyright: ignore[reportMissingImports]
from adtof_pytorch.model import calculate_n_bins, create_frame_rnn_model, load_pytorch_weights  # pyright: ignore[reportMissingImports]

CHUNK = 1000  # frames: 10 s (smaller saves no more)
MARGIN = 8  # frames either side: the convolutions see 4 (four 3x3 layers; pooled across frequency only)


def strengths(model: torch.nn.Module, x: torch.Tensor) -> np.ndarray:
    """The model's output for a song's spectrogram ([1, time, bins, 1]): [time, classes]."""
    frames = x.shape[1]
    parts = []
    with torch.no_grad():
        for start in range(0, frames, CHUNK):
            low, high = max(start - MARGIN, 0), min(start + CHUNK + MARGIN, frames)
            y = x[:, low:high].permute(0, 3, 1, 2)
            for block in model.cnn_blocks:
                y = block(y)
            y = y.permute(0, 2, 3, 1).reshape(1, high - low, -1)
            parts.append(y[:, start - low:start - low + min(CHUNK, frames - start)])
        y = torch.cat(parts, dim=1)
        if getattr(model, "context_layer", None) is not None:
            y = model.context_layer(y)
        for gru in model.gru_layers:
            y, _ = gru(y)
        return torch.sigmoid(model.output_layer(y))[0].numpy()


def transcribe(audio: str, out: str) -> np.ndarray:
    model = create_frame_rnn_model(calculate_n_bins())
    model.eval()
    weights = get_default_weights_path()
    if not weights or not all(hasattr(model, n) for n in ("cnn_blocks", "gru_layers", "output_layer")):
        return transcribe_to_midi(audio, out + ".mid", return_activations=True, device="cpu")[0]  # (not as known)
    model = load_pytorch_weights(model, weights, strict=False)
    return strengths(model, load_audio_for_model(audio))


if __name__ == "__main__":
    np.save(sys.argv[2], transcribe(sys.argv[1], sys.argv[2]).astype(np.float16))
