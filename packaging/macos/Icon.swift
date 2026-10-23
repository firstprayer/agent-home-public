import AppKit

let folder = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
        let transform = AffineTransform(scale: CGFloat(pixels) / 1024)
        (transform as NSAffineTransform).concat()
        let green = NSColor(srgbRed: 0.125, green: 0.298, blue: 0.227, alpha: 1)
        green.set()
        NSBezierPath(roundedRect: NSRect(x: 80, y: 80, width: 864, height: 864),
            xRadius: 196, yRadius: 196).fill()
        NSColor(srgbRed: 0.97, green: 0.973, blue: 0.945, alpha: 1).set()
        let house = NSBezierPath()
        house.move(to: NSPoint(x: 296, y: 534))
        house.line(to: NSPoint(x: 512, y: 706))
        house.line(to: NSPoint(x: 728, y: 534))
        house.line(to: NSPoint(x: 728, y: 318))
        house.curve(to: NSPoint(x: 685, y: 275), controlPoint1: NSPoint(x: 728, y: 294), controlPoint2: NSPoint(x: 709, y: 275))
        house.line(to: NSPoint(x: 577, y: 275))
        house.line(to: NSPoint(x: 577, y: 448))
        house.line(to: NSPoint(x: 447, y: 448))
        house.line(to: NSPoint(x: 447, y: 275))
        house.line(to: NSPoint(x: 339, y: 275))
        house.curve(to: NSPoint(x: 296, y: 318), controlPoint1: NSPoint(x: 315, y: 275), controlPoint2: NSPoint(x: 296, y: 294))
        house.close()
        house.fill()
        green.set()
        NSBezierPath(ovalIn: NSRect(x: 598, y: 598, width: 216, height: 216)).fill()
        NSColor(srgbRed: 0.788, green: 0.949, blue: 0.482, alpha: 1).set()
        NSBezierPath(ovalIn: NSRect(x: 620, y: 620, width: 172, height: 172)).fill()
        NSGraphicsContext.restoreGraphicsState()
        let suffix = scale == 2 ? "@2x" : ""
        let name = "icon_\(size)x\(size)\(suffix).png"
        try bitmap.representation(using: .png, properties: [:])!.write(to: folder.appendingPathComponent(name))
    }
}
