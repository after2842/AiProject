import sounddevice as sd
import numpy as np
import time
import asyncio 

        
def monitor_audio_levels():
    print("Monitoring audio levels... (Press Ctrl+C to stop)")
    audio_queue = asyncio.Queue()
    try:
        stream = sd.InputStream(
            channels=1,
            samplerate=24000,
            dtype="int16",
            blocksize=1024
        )
        
        stream.start()
        
        while True:
            if stream.read_available >= 1024:
                data, overflowed = stream.read(1024)
                
                # Calculate volume level
                #volume = np.sqrt(np.mean(data.astype(np.float32)**2))
                
                # Create visual indicator
                #bar_length = int(volume * 50)  # Scale to 50 characters
                #bar = "█" * bar_length + "░" * (50 - bar_length)
                
                print(f"\rVolume: ", end="", flush=True)
                
                if overflowed:
                    print("\n⚠️ Buffer overflow!")
            
            #time.sleep(0.05)  # 50ms update rate
            
    except KeyboardInterrupt:
        print("\n✅ Monitoring stopped")
    except Exception as e:
        print(f"\n❌ Error: {e}")
    finally:
        stream.stop()
        stream.close()

# Run the monitor
monitor_audio_levels()