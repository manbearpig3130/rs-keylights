"""drums_activations.py DRUMS.WAV OUT.npy: ADTOF's strengths for a drum track (100 a second; kick, snare, tom,
hi-hat, cymbal), saved as float16. Runs in the drum transcriber's own Python (tools/drums-setup.sh), not
rs-keylights': rs_keylights.drums starts it."""
import sys

import numpy as np
from adtof_pytorch import transcribe_to_midi  # pyright: ignore[reportMissingImports]  (its own Python's)

if __name__ == "__main__":
    strengths = transcribe_to_midi(sys.argv[1], sys.argv[2] + ".mid", return_activations=True, device="cpu")
    np.save(sys.argv[2], strengths[0].astype(np.float16))
