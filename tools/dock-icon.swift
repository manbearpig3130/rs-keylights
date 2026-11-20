// dock-icon.swift OUT.png: draws rs-keylights' app icon (1024x1024), for tools/dock-app.sh. An octave of keys with
// three lit, each by a note gem falling onto it in Rocksmith's string colours.
import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

let size = 1024
let space = CGColorSpace(name: CGColorSpace.sRGB)!
let ctx = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8, bytesPerRow: 0, space: space,
                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
ctx.translateBy(x: 0, y: CGFloat(size))
ctx.scaleBy(x: 1, y: -1)  // y down, as the layout below reads

func rgb(_ hex: UInt32, _ alpha: CGFloat = 1) -> CGColor {
    CGColor(srgbRed: CGFloat(hex >> 16 & 0xff) / 255, green: CGFloat(hex >> 8 & 0xff) / 255,
            blue: CGFloat(hex & 0xff) / 255, alpha: alpha)
}

func gradient(_ colors: [CGColor], in rect: CGRect, path: CGPath) {
    ctx.saveGState()
    ctx.addPath(path)
    ctx.clip()
    let g = CGGradient(colorsSpace: space, colors: colors as CFArray, locations: nil)!
    ctx.drawLinearGradient(g, start: CGPoint(x: rect.midX, y: rect.minY), end: CGPoint(x: rect.midX, y: rect.maxY),
                           options: [])
    ctx.restoreGState()
}

func rounded(_ rect: CGRect, _ r: CGFloat) -> CGPath {
    CGPath(roundedRect: rect, cornerWidth: r, cornerHeight: r, transform: nil)
}

// keys with square tops and rounded bottoms
func key(_ rect: CGRect, _ r: CGFloat) -> CGPath {
    let p = CGMutablePath()
    p.move(to: CGPoint(x: rect.minX, y: rect.minY))
    p.addLine(to: CGPoint(x: rect.maxX, y: rect.minY))
    p.addArc(tangent1End: CGPoint(x: rect.maxX, y: rect.maxY), tangent2End: CGPoint(x: rect.minX, y: rect.maxY), radius: r)
    p.addArc(tangent1End: CGPoint(x: rect.minX, y: rect.maxY), tangent2End: CGPoint(x: rect.minX, y: rect.minY), radius: r)
    p.closeSubpath()
    return p
}

// The body: macOS's icon grid, 824 in 1024
let body = CGRect(x: 100, y: 100, width: 824, height: 824)
let bodyPath = rounded(body, 185)
ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: 10), blur: 24, color: rgb(0x000000, 0.45))
ctx.addPath(bodyPath)
ctx.setFillColor(rgb(0x14172a))
ctx.fillPath()
ctx.restoreGState()
gradient([rgb(0x252a44), rgb(0x0e1020)], in: body, path: bodyPath)

// The keyboard's case
let caseRect = CGRect(x: 150, y: 560, width: 724, height: 300)
gradient([rgb(0x3a3f55), rgb(0x23263a)], in: caseRect, path: rounded(caseRect, 34))
let keysTop: CGFloat = 592
let keysLeft: CGFloat = 172
let keysWidth: CGFloat = 680
let whiteW = keysWidth / 7
let whiteH: CGFloat = 246
let gap: CGFloat = 7

// The lit keys (white key index) and their colours: Rocksmith's red, yellow and blue strings
let lit: [(Int, UInt32, UInt32)] = [(0, 0xff4a3d, 0xffb3a6), (2, 0xffd23a, 0xfff0b0), (4, 0x3d8bff, 0xb0d2ff)]
func litColour(_ i: Int) -> (UInt32, UInt32)? {
    lit.first { $0.0 == i }.map { ($0.1, $0.2) }
}

// Falling notes: a trail down to each lit key, its gem just above the keys
for (i, colour, light) in lit {
    let x = keysLeft + CGFloat(i) * whiteW + whiteW / 2
    let lane = CGRect(x: x - 30, y: 150, width: 60, height: keysTop - 150)
    gradient([rgb(colour, 0), rgb(colour, 0.32)], in: lane, path: rounded(lane, 30))
    let drop: CGFloat = [0: 130, 2: 40, 4: 210][i]!  // how far from the keys each still is
    let gem = CGRect(x: x - 42, y: keysTop - 70 - drop, width: 84, height: 52)
    ctx.saveGState()
    ctx.setShadow(offset: .zero, blur: 36, color: rgb(colour, 0.95))
    ctx.addPath(rounded(gem, 16))
    ctx.setFillColor(rgb(colour))
    ctx.fillPath()
    ctx.restoreGState()
    gradient([rgb(light), rgb(colour)], in: gem, path: rounded(gem, 16))
    let shine = CGRect(x: gem.minX + 10, y: gem.minY + 6, width: gem.width - 20, height: 14)
    ctx.addPath(rounded(shine, 7))
    ctx.setFillColor(rgb(0xffffff, 0.45))
    ctx.fillPath()
}

// White keys
for i in 0..<7 {
    let rect = CGRect(x: keysLeft + CGFloat(i) * whiteW + gap / 2, y: keysTop, width: whiteW - gap, height: whiteH)
    let path = key(rect, 14)
    if let (colour, light) = litColour(i) {
        ctx.saveGState()
        ctx.setShadow(offset: .zero, blur: 44, color: rgb(colour, 0.9))
        ctx.addPath(path)
        ctx.setFillColor(rgb(colour))
        ctx.fillPath()
        ctx.restoreGState()
        gradient([rgb(light), rgb(colour)], in: rect, path: path)
    } else {
        gradient([rgb(0xffffff), rgb(0xdcdde4)], in: rect, path: path)
    }
}

// Black keys, after C, D, F, G and A
for i in [0, 1, 3, 4, 5] {
    let w: CGFloat = 58
    let x = keysLeft + CGFloat(i + 1) * whiteW - w / 2
    let rect = CGRect(x: x, y: keysTop, width: w, height: 150)
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: 4), blur: 6, color: rgb(0x000000, 0.5))
    ctx.addPath(key(rect, 10))
    ctx.setFillColor(rgb(0x12131a))
    ctx.fillPath()
    ctx.restoreGState()
    gradient([rgb(0x1a1c26), rgb(0x3b3e4c)], in: rect.insetBy(dx: 7, dy: 0).offsetBy(dx: 0, dy: -4),
             path: key(rect.insetBy(dx: 7, dy: 0).offsetBy(dx: 0, dy: -4), 8))
}

// The case's top edge over the keys' tops
let lip = CGRect(x: 150, y: 578, width: 724, height: 16)
ctx.addRect(lip)
ctx.setFillColor(rgb(0x2b2f44))
ctx.fillPath()

let out = URL(fileURLWithPath: CommandLine.arguments[1])
let dest = CGImageDestinationCreateWithURL(out as CFURL, UTType.png.identifier as CFString, 1, nil)!
CGImageDestinationAddImage(dest, ctx.makeImage()!, nil)
guard CGImageDestinationFinalize(dest) else { fatalError("couldn't write \(out.path)") }
