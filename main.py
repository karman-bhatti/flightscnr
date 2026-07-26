import sys
import os

# Add the flightscnr folder to sys.path so modules resolve correctly
base_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(base_dir, "flightscnr"))

# Start the application
if __name__ == "__main__":
    import flightscnr
