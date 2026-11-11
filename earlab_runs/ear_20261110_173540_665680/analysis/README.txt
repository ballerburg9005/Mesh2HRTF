Mesh2HRTF NumCalc produced this 3D linear-wave response.

response.csv / response.png show H(f) as microphone pressure divided by
free-field point-source pressure at the same true source-to-mic distance.

The model assumes rigid PLA-like boundaries, open air connected to the
source, ideal point source, and an ideal zero-volume non-loading microphone.
It does NOT yet simulate the microphone capsule diaphragm impedance, tube
viscothermal losses, PLA mechanical vibration, or nonlinear distortion.
If the STL does not contain an open, air-connected canal and tube, its
results CANNOT diagnose those cavities. Validate mesh geometry first.

The WAV audition interpolates a possibly sparse frequency spectrum and is
only a qualitative comparison, NOT a verified full-band physical HRIR.
The source distance does not change boundary mesh size in this solver.
