import Foundation
import PDFKit
import AppKit
let a=CommandLine.arguments
let doc=PDFDocument(url:URL(fileURLWithPath:a[1]))!
let n=a.count>3 ? Int(a[3])! : 1
let page=doc.page(at:n-1)!, box=doc.page(at:n-1)!.bounds(for:.mediaBox)
let img=page.thumbnail(of:NSSize(width:box.width*2,height:box.height*2),for:.mediaBox)
var rect=CGRect(origin:.zero,size:img.size)
let cg=img.cgImage(forProposedRect:&rect,context:nil,hints:nil)!
try NSBitmapImageRep(cgImage:cg).representation(using:.png,properties:[:])!.write(to:URL(fileURLWithPath:a[2]))
