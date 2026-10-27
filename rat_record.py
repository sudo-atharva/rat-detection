"""Record-only version of the GUI: live video + MPU6050 tap/jump graph during the run,
rat tracking and data overlay done after the run (video_processed.mp4)."""
from rat_gui import main

if __name__ == "__main__":
    main(live_tracking=False)
