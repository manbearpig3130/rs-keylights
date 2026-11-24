// The MacBook's headphone output's volume: read (no argument), or set (a scalar 0...1), per channel, in dB too.
import CoreAudio
import Foundation

func devices() -> [AudioDeviceID] {
    var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDevices, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size)
    var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
    AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &ids)
    return ids
}
func name(_ id: AudioDeviceID) -> String {
    var addr = AudioObjectPropertyAddress(mSelector: kAudioObjectPropertyName, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    var n: Unmanaged<CFString>?
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &n)
    return n?.takeRetainedValue() as String? ?? ""
}
func dataSource(_ id: AudioDeviceID) -> String {
    var addr = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyDataSource, mScope: kAudioDevicePropertyScopeOutput, mElement: kAudioObjectPropertyElementMain)
    var src: UInt32 = 0
    var size = UInt32(4)
    if AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &src) != 0 { return "?" }
    return String(format: "%c%c%c%c", (src >> 24) & 255, (src >> 16) & 255, (src >> 8) & 255, src & 255)
}
let wanted = CommandLine.arguments.count > 1 ? Float32(CommandLine.arguments[1]) : nil
for id in devices() where name(id).contains("Headphones") || dataSource(id) == "hdpn" {
    for ch: UInt32 in [0, 1, 2] {
        var addr = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyVolumeScalar, mScope: kAudioDevicePropertyScopeOutput, mElement: ch)
        guard AudioObjectHasProperty(id, &addr) else { continue }
        if var v = wanted { AudioObjectSetPropertyData(id, &addr, 0, nil, 4, &v) }
        var v: Float32 = 0
        var size = UInt32(4)
        AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &v)
        var dbAddr = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyVolumeDecibels, mScope: kAudioDevicePropertyScopeOutput, mElement: ch)
        var d: Float32 = 0
        AudioObjectGetPropertyData(id, &dbAddr, 0, nil, &size, &d)
        print("\(name(id)) [\(dataSource(id))] channel \(ch): volume \(v) (\(d) dB)")
    }
}
