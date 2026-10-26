import Foundation
import PDFKit
import Vision
import AppKit
let args=CommandLine.arguments
if args.count != 3 { fatalError("usage: ocr_pdf.swift input.pdf output.json") }
let pdf=PDFDocument(url:URL(fileURLWithPath:args[1]))!
var pages:[[String:Any]]=[]
for i in 0..<pdf.pageCount {
 let page=pdf.page(at:i)!
 let bounds=page.bounds(for:.mediaBox)
 let image=page.thumbnail(of:NSSize(width:bounds.width*2.5,height:bounds.height*2.5),for:.mediaBox)
 var rect=CGRect(origin:.zero,size:image.size)
 let cg=image.cgImage(forProposedRect:&rect,context:nil,hints:nil)!
 let request=VNRecognizeTextRequest()
 request.recognitionLevel = .accurate
 request.recognitionLanguages=["ru-RU","en-US"]
 request.usesLanguageCorrection=true
 try VNImageRequestHandler(cgImage:cg).perform([request])
 let observations=request.results ?? []
 let text=observations.compactMap{$0.topCandidates(1).first?.string}.joined(separator:"\n")
 pages.append(["page":i+1,"text":text,"ocr_engine":"macOS Vision","mean_confidence":observations.isEmpty ? 0 : observations.compactMap{$0.topCandidates(1).first?.confidence}.reduce(0,+)/Float(observations.count)])
 if i==0,let rep=NSBitmapImageRep(cgImage:cg).representation(using:.png,properties:[:]) { try rep.write(to:URL(fileURLWithPath:args[2]+".first.png")) }
}
let data=try JSONSerialization.data(withJSONObject:pages,options:[.prettyPrinted,.sortedKeys,.withoutEscapingSlashes]); try data.write(to:URL(fileURLWithPath:args[2]))
print("OCR \(pdf.pageCount) pages: \(args[1])")
