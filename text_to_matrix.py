# -*- coding: utf-8 -*-
#This is an edited version of https://gist.github.com/dbader/5488053

import freetype

MAX_MATRIX_HEIGHT = 7
ARIAL_FONT_FILE_NAME = 'Arial.ttf'
FONT_SIZE = 9

class Bitmap(object):
    """
    A 2D bitmap image represented as a list of byte values. Each byte indicates the state
    of a single pixel in the bitmap. A value of 0 indicates that the pixel is `off`
    and any other value indicates that it is `on`.
    """
    def __init__(self, width, height, pixels=None):
        self.width = int(width + 0.5)
        self.height = int(height + 0.5)
        self.pixels = pixels or bytearray(self.width * self.height)

    def __repr__(self):
        """Return a string representation of the bitmap's pixels."""
        rows = ''
        for y in range(self.height):
            for x in range(self.width):
                rows += '#' if self.pixels[y * self.width + x] else '.'
            rows += '\n'
        return rows

    def bitblt(self, src, x, y):
        """Copy all pixels from `src` into this bitmap"""
        srcpixel = int(0)
        dstpixel = int(y * self.width + x)
        row_offset = int(self.width - src.width)

        for sy in range(src.height):
            for sx in range(src.width):
                # Perform an OR operation on the destination pixel and the source pixel
                # because glyph bitmaps may overlap if character kerning is applied, e.g.
                # in the string "AVA", the "A" and "V" glyphs must be rendered with
                # overlapping bounding boxes.
                self.pixels[dstpixel] = self.pixels[dstpixel] or src.pixels[srcpixel]
                srcpixel += 1
                dstpixel += 1
            dstpixel += row_offset

    def get_matrix(self):
        """Return a matrix representation of the bitmap's pixels."""
        ret = [[] for i in range(min(self.height, MAX_MATRIX_HEIGHT))]
        for y in range(min(self.height, MAX_MATRIX_HEIGHT)):
            for x in range(self.width):
                ret[y].append(4 if self.pixels[y * self.width + x] else 1)
        while len(ret) < MAX_MATRIX_HEIGHT:
            ret.append([1 for i in range(self.width)])
        return ret


class Glyph(object):
    def __init__(self, pixels, width, height, top, advance_width):
        self.bitmap = Bitmap(width, height, pixels)

        # The glyph bitmap's top-side bearing, i.e. the vertical distance from the
        # baseline to the bitmap's top-most scanline.
        self.top = top

        # Ascent and descent determine how many pixels the glyph extends
        # above or below the baseline.
        self.descent = max(0, self.height - self.top)
        self.ascent = max(0, max(self.top, self.height) - self.descent)

        # The advance width determines where to place the next character horizontally,
        # that is, how many pixels we move to the right to draw the next glyph.
        self.advance_width = advance_width

    @property
    def width(self):
        return self.bitmap.width

    @property
    def height(self):
        return self.bitmap.height

    @staticmethod
    def from_glyphslot(slot):
        """Construct and return a Glyph object from a FreeType GlyphSlot."""
        pixels = Glyph.unpack_mono_bitmap(slot.bitmap)
        width, height = slot.bitmap.width, slot.bitmap.rows
        top = slot.bitmap_top

        # The advance width is given in FreeType's 26.6 fixed point format,
        # which means that the pixel values are multiples of 64.
        advance_width = slot.advance.x / 64

        return Glyph(pixels, width, height, top, advance_width)

    @staticmethod
    def unpack_mono_bitmap(bitmap):
        """
        Unpack a freetype FT_LOAD_TARGET_MONO glyph bitmap into a bytearray where each
        pixel is represented by a single byte.
        """
        # Allocate a bytearray of sufficient size to hold the glyph bitmap.
        data = bytearray(bitmap.rows * bitmap.width)

        # Iterate over every byte in the glyph bitmap. Note that we're not
        # iterating over every pixel in the resulting unpacked bitmap --
        # we're iterating over the packed bytes in the input bitmap.
        for y in range(bitmap.rows):
            for byte_index in range(bitmap.pitch):

                # Read the byte that contains the packed pixel data.
                byte_value = bitmap.buffer[y * bitmap.pitch + byte_index]

                # We've processed this many bits (=pixels) so far. This determines
                # where we'll read the next batch of pixels from.
                num_bits_done = byte_index * 8

                # Pre-compute where to write the pixels that we're going
                # to unpack from the current byte in the glyph bitmap.
                rowstart = y * bitmap.width + byte_index * 8

                # Iterate over every bit (=pixel) that's still a part of the
                # output bitmap. Sometimes we're only unpacking a fraction of a byte
                # because glyphs may not always fit on a byte boundary. So we make sure
                # to stop if we unpack past the current row of pixels.
                for bit_index in range(min(8, bitmap.width - num_bits_done)):

                    # Unpack the next pixel from the current glyph byte.
                    bit = byte_value & (1 << (7 - bit_index))

                    # Write the pixel to the output bytearray. We ensure that `off`
                    # pixels have a value of 0 and `on` pixels have a value of 1.
                    data[rowstart + bit_index] = 1 if bit else 0

        return data


class Font(object):
    def __init__(self, filename, size):
        self.face = freetype.Face(filename)
        self.face.set_pixel_sizes(0, size)

    def glyph_for_character(self, char):
        # Let FreeType load the glyph for the given character and tell it to render
        # a monochromatic bitmap representation.
        self.face.load_char(char, freetype.FT_LOAD_RENDER | freetype.FT_LOAD_TARGET_MONO)
        return Glyph.from_glyphslot(self.face.glyph)

    def render_character(self, char):
        glyph = self.glyph_for_character(char)
        return glyph.bitmap

    def kerning_offset(self, previous_char, char):
        """
        Return the horizontal kerning offset in pixels when rendering `char`
        after `previous_char`.
        Use the resulting offset to adjust the glyph's drawing position to
        reduces extra diagonal whitespace, for example in the string "AV" the
        bitmaps for "A" and "V" may overlap slightly with some fonts. In this
        case the glyph for "V" has a negative horizontal kerning offset as it is
        moved slightly towards the "A".
        """
        kerning = self.face.get_kerning(previous_char, char)

        # The kerning offset is given in FreeType's 26.6 fixed point format,
        # which means that the pixel values are multiples of 64.
        return kerning.x / 64

    def text_dimensions(self, text):
        """Return (width, height, baseline) of `text` rendered in the current font."""
        width = 0
        max_ascent = 0
        max_descent = 0
        previous_char = None

        # For each character in the text string we get the glyph
        # and update the overall dimensions of the resulting bitmap.
        for char in text:
            glyph = self.glyph_for_character(char)
            max_ascent = max(max_ascent, glyph.ascent)
            max_descent = max(max_descent, glyph.descent)
            kerning_x = self.kerning_offset(previous_char, char)

            # With kerning, the advance width may be less than the width of the glyph's bitmap.
            # Make sure we compute the total width so that all of the glyph's pixels
            # fit into the returned dimensions.
            width += max(glyph.advance_width + kerning_x, glyph.width + kerning_x)

            previous_char = char

        height = max_ascent + max_descent
        return (width, height, max_descent)

    def render_text(self, text, width=None, height=None, baseline=None):
        """
        Render the given `text` into a Bitmap and return it.
        If `width`, `height`, and `baseline` are not specified they are computed using
        the `text_dimensions' method.
        """
        if None in (width, height, baseline):
            width, height, baseline = self.text_dimensions(text)

        x = 0
        previous_char = None
        outbuffer = Bitmap(width, height)

        for char in text:
            glyph = self.glyph_for_character(char)

            # Take kerning information into account before we render the
            # glyph to the output bitmap.
            x += self.kerning_offset(previous_char, char)

            # The vertical drawing position should place the glyph
            # on the baseline as intended.
            y = height - glyph.ascent - baseline

            outbuffer.bitblt(glyph.bitmap, x, y)

            x += glyph.advance_width
            previous_char = char

        return outbuffer


def get_arial_font_matrix(text):
    fnt = Font(ARIAL_FONT_FILE_NAME, FONT_SIZE)
    return fnt.render_text(text).get_matrix()